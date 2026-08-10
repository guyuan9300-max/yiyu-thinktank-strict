from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
import threading
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator

from .contracts import CLOUD_CONTRACT, LOCAL_CONTRACT, DatabaseRole, SchemaContract
from .ids import new_id, sha256_text, utc_now
from .physical_schema import ddl_from_manifest, user_tables
from .project_scope import seed_project_scope_decision


_SQL_AUDIT_LOCK = threading.Lock()
_SQL_STRING_LITERAL = re.compile(r"'(?:''|[^'])*'")
_SQL_BLOB_LITERAL = re.compile(r"\b[xX]'[0-9a-fA-F]*'")
_SQL_NUMBER_LITERAL = re.compile(r"(?<![\w.])-?\d+(?:\.\d+)?(?![\w.])")
_SQL_TABLE_REFERENCE = re.compile(
    r'\b(?:FROM|JOIN|INTO|UPDATE|TABLE)\s+(?:"([^"]+)"|([A-Za-z_][A-Za-z0-9_]*))',
    re.IGNORECASE,
)


@dataclass(frozen=True)
class DatabaseIdentity:
    database_role: str
    schema_family: str
    contract_version: str
    manifest_hash: str
    migration_set_hash: str
    build_id: str
    database_generation_id: str
    created_at: str


def contract_for(role: DatabaseRole) -> SchemaContract:
    return LOCAL_CONTRACT if role == "local" else CLOUD_CONTRACT


def ddl_for(role: DatabaseRole) -> str:
    return ddl_from_manifest(contract_for(role).raw)


def migration_set_hash(role: DatabaseRole) -> str:
    return hashlib.sha256(ddl_for(role).encode("utf-8")).hexdigest()


def _configure(connection: sqlite3.Connection, *, read_only: bool = False) -> None:
    connection.execute("PRAGMA foreign_keys = ON")
    connection.execute("PRAGMA trusted_schema = OFF")
    connection.execute("PRAGMA busy_timeout = 10000")
    if read_only:
        connection.execute("PRAGMA query_only = ON")
    else:
        connection.execute("PRAGMA journal_mode = WAL")
        connection.execute("PRAGMA synchronous = FULL")


def _user_tables(connection: sqlite3.Connection) -> set[str]:
    return user_tables(connection)


def _verify_layout(
    connection: sqlite3.Connection,
    role: DatabaseRole,
) -> None:
    contract = contract_for(role)
    actual_tables = _user_tables(connection)
    expected_tables = set(contract.allowed_tables)
    if actual_tables != expected_tables:
        extra = sorted(actual_tables - expected_tables)
        missing = sorted(expected_tables - actual_tables)
        raise RuntimeError(
            f"strict {role} schema table mismatch extra={extra} missing={missing}"
        )
    for table_name, required_keys in contract.required_keys.items():
        columns = {
            str(row[1])
            for row in connection.execute(
                f'PRAGMA table_info("{table_name}")'
            ).fetchall()
        }
        missing_columns = sorted(required_keys - columns)
        if missing_columns:
            raise RuntimeError(
                f"strict {role} schema required columns missing "
                f"table={table_name} columns={missing_columns}"
            )


def _identity(connection: sqlite3.Connection) -> DatabaseIdentity:
    row = connection.execute(
        """
        SELECT database_role, schema_family, CAST(version AS TEXT), manifest_hash,
               migration_set_hash, COALESCE(build_id, id),
               database_generation_id, created_at
        FROM schema_versions
        WHERE status = 'active'
        ORDER BY activated_at DESC
        LIMIT 1
        """
    ).fetchone()
    if row is None:
        raise RuntimeError("database has no active schema build identity")
    return DatabaseIdentity(
        database_role=str(row[0]),
        schema_family=str(row[1]),
        contract_version=str(row[2]),
        manifest_hash=str(row[3]),
        migration_set_hash=str(row[4]),
        build_id=str(row[5]),
        database_generation_id=str(row[6]),
        created_at=str(row[7]),
    )


def _verify_identity(identity: DatabaseIdentity, role: DatabaseRole) -> None:
    contract = contract_for(role)
    expected_migration_hash = migration_set_hash(role)
    mismatches: list[str] = []
    if identity.database_role != contract.database_role:
        mismatches.append("database_role")
    if identity.schema_family != contract.schema_family:
        mismatches.append("schema_family")
    if identity.contract_version != contract.contract_version:
        mismatches.append("contract_version")
    if identity.manifest_hash != contract.manifest_hash:
        mismatches.append("manifest_hash")
    if identity.migration_set_hash != expected_migration_hash:
        mismatches.append("migration_set_hash")
    if mismatches:
        raise RuntimeError(
            "strict database identity mismatch: " + ", ".join(mismatches)
        )


def initialize_database(path: Path, role: DatabaseRole) -> DatabaseIdentity:
    path = path.resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    existed = path.exists()
    connection = sqlite3.connect(path)
    try:
        _configure(connection)
        current_tables = _user_tables(connection)
        contract = contract_for(role)
        if current_tables:
            _verify_layout(connection, role)
            identity = _identity(connection)
            _verify_identity(identity, role)
            violations = connection.execute("PRAGMA foreign_key_check").fetchall()
            if violations:
                raise RuntimeError(
                    f"strict {role} database has {len(violations)} foreign key violations"
                )
            return identity

        if existed and path.stat().st_size > 0:
            raise RuntimeError(
                f"refusing to initialize non-empty database without strict tables: {path}"
            )

        now = utc_now()
        build_id = new_id()
        generation_id = new_id()
        migration_id = new_id()
        fence_id = new_id()
        first_write_id = new_id()
        first_operation_id = new_id()
        writer_id = f"genesis:{role}:{build_id}"
        ddl = ddl_for(role)
        ddl_hash = migration_set_hash(role)

        connection.executescript("BEGIN IMMEDIATE;\n" + ddl)
        connection.execute(
            """
            INSERT INTO schema_versions (
                id, engine, version, checksum, status, database_role,
                schema_family, manifest_hash, migration_set_hash, build_id,
                created_at, activated_at, authority_role, origin_instance_id,
                database_generation_id
            ) VALUES (?, 'sqlite', ?, ?, 'active', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                build_id,
                int(contract.contract_version),
                ddl_hash,
                contract.database_role,
                contract.schema_family,
                contract.manifest_hash,
                ddl_hash,
                build_id,
                now,
                now,
                role,
                generation_id,
                generation_id,
            ),
        )
        seed_project_scope_decision(
            connection,
            schema_version_id=build_id,
        )
        connection.execute(
            """
            INSERT INTO migration_ledger (
                id, schema_version_id, step, checksum, status, from_version,
                to_version, code_hash, started_at, completed_at, rollback_ref,
                origin_instance_id, created_at, integrity_hash, authority_role
            ) VALUES (?, ?, 'genesis', ?, 'applied', NULL, ?, ?, ?, ?, NULL, ?, ?, ?, ?)
            """,
            (
                migration_id,
                build_id,
                ddl_hash,
                contract.manifest_hash,
                ddl_hash,
                now,
                now,
                generation_id,
                now,
                sha256_text(f"{build_id}|genesis|{ddl_hash}|{now}"),
                role,
            ),
        )
        connection.execute(
            """
            INSERT INTO write_fences (
                id, fence_epoch, writer_id, lease_until, state, database_role,
                reason, acquired_at, released_at, authority_role,
                origin_instance_id
            ) VALUES (?, 1, ?, '9999-12-31T23:59:59.999Z', 'active', ?,
                      'genesis', ?, NULL, ?, ?)
            """,
            (fence_id, writer_id, contract.database_role, now, role, generation_id),
        )
        connection.execute(
            """
            INSERT INTO first_write_ledger (
                id, schema_version_id, operation_id, fence_id, state,
                committed_at, proof_hash, origin_instance_id, created_at,
                integrity_hash, authority_role
            ) VALUES (?, ?, ?, ?, 'committed', ?, ?, ?, ?, ?, ?)
            """,
            (
                first_write_id,
                build_id,
                None,
                fence_id,
                now,
                sha256_text(f"{build_id}|{first_operation_id}|{fence_id}|{now}"),
                generation_id,
                now,
                sha256_text(f"{first_write_id}|{build_id}|{now}"),
                role,
            ),
        )
        connection.execute(f"PRAGMA user_version = {int(contract.contract_version)}")
        _verify_layout(connection, role)
        identity = _identity(connection)
        _verify_identity(identity, role)
        violations = connection.execute("PRAGMA foreign_key_check").fetchall()
        if violations:
            raise RuntimeError(
                f"strict {role} genesis has {len(violations)} foreign key violations"
            )
        connection.execute("COMMIT")
        return identity
    except Exception:
        if connection.in_transaction:
            connection.execute("ROLLBACK")
        if not existed and path.exists():
            connection.close()
            path.unlink(missing_ok=True)
            Path(f"{path}-wal").unlink(missing_ok=True)
            Path(f"{path}-shm").unlink(missing_ok=True)
            raise
        raise
    finally:
        try:
            connection.close()
        except Exception:
            pass



def migrate_database(
    path: Path,
    role: DatabaseRole,
    *,
    expected_from_manifest_hash: str,
) -> DatabaseIdentity:
    del path, role, expected_from_manifest_hash
    raise RuntimeError(
        "blueprint-88 cutover requires the offline rebuild migrator; "
        "in-place runtime migration is forbidden"
    )


_DDL_ACTIONS = frozenset(
    action
    for action in (
        getattr(sqlite3, "SQLITE_CREATE_INDEX", None),
        getattr(sqlite3, "SQLITE_CREATE_TABLE", None),
        getattr(sqlite3, "SQLITE_CREATE_TEMP_INDEX", None),
        getattr(sqlite3, "SQLITE_CREATE_TEMP_TABLE", None),
        getattr(sqlite3, "SQLITE_CREATE_TEMP_TRIGGER", None),
        getattr(sqlite3, "SQLITE_CREATE_TEMP_VIEW", None),
        getattr(sqlite3, "SQLITE_CREATE_TRIGGER", None),
        getattr(sqlite3, "SQLITE_CREATE_VIEW", None),
        getattr(sqlite3, "SQLITE_DROP_INDEX", None),
        getattr(sqlite3, "SQLITE_DROP_TABLE", None),
        getattr(sqlite3, "SQLITE_DROP_TEMP_INDEX", None),
        getattr(sqlite3, "SQLITE_DROP_TEMP_TABLE", None),
        getattr(sqlite3, "SQLITE_DROP_TEMP_TRIGGER", None),
        getattr(sqlite3, "SQLITE_DROP_TEMP_VIEW", None),
        getattr(sqlite3, "SQLITE_DROP_TRIGGER", None),
        getattr(sqlite3, "SQLITE_DROP_VIEW", None),
        getattr(sqlite3, "SQLITE_ALTER_TABLE", None),
        getattr(sqlite3, "SQLITE_REINDEX", None),
    )
    if action is not None
)

_TABLE_ACTIONS = frozenset(
    action
    for action in (
        getattr(sqlite3, "SQLITE_READ", None),
        getattr(sqlite3, "SQLITE_INSERT", None),
        getattr(sqlite3, "SQLITE_UPDATE", None),
        getattr(sqlite3, "SQLITE_DELETE", None),
    )
    if action is not None
)


def _authorizer(role: DatabaseRole):
    allowed = contract_for(role).allowed_tables

    def authorize(
        action: int,
        arg1: str | None,
        arg2: str | None,
        database_name: str | None,
        trigger_name: str | None,
    ) -> int:
        del arg2, trigger_name
        if action in _DDL_ACTIONS:
            return sqlite3.SQLITE_DENY
        if action in {
            getattr(sqlite3, "SQLITE_ATTACH", -1),
            getattr(sqlite3, "SQLITE_DETACH", -1),
        }:
            return sqlite3.SQLITE_DENY
        if action == getattr(sqlite3, "SQLITE_PRAGMA", -1):
            if (arg1 or "").lower() in {
                "writable_schema",
                "legacy_alter_table",
                "foreign_keys",
            }:
                return sqlite3.SQLITE_DENY
        if action in _TABLE_ACTIONS and database_name == "main":
            table = arg1 or ""
            if table and not table.startswith("sqlite_") and table not in allowed:
                return sqlite3.SQLITE_DENY
        return sqlite3.SQLITE_OK

    return authorize


def _install_sql_audit(
    connection: sqlite3.Connection,
    *,
    role: DatabaseRole,
    database_path: Path,
) -> None:
    audit_file = os.environ.get("YIYU_STRICT_SQL_AUDIT_FILE", "").strip()
    if not audit_file:
        return
    output_path = Path(audit_file).expanduser().resolve()
    output_path.parent.mkdir(parents=True, exist_ok=True)

    def trace(statement: str) -> None:
        stripped = statement.strip()
        if not stripped:
            return
        kind = stripped.split(None, 1)[0].upper()
        shape = _SQL_BLOB_LITERAL.sub("?", stripped)
        shape = _SQL_STRING_LITERAL.sub("?", shape)
        shape = _SQL_NUMBER_LITERAL.sub("?", shape)
        shape = " ".join(shape.split())
        tables = sorted(
            {
                next(value for value in match.groups() if value)
                for match in _SQL_TABLE_REFERENCE.finditer(stripped)
            }
        )
        record = {
            "at": utc_now(),
            "pid": os.getpid(),
            "databaseRole": role,
            "databasePath": str(database_path.resolve()),
            "statementKind": kind,
            "tables": tables,
            "ddl": kind in {"ALTER", "CREATE", "DROP", "REINDEX", "VACUUM"},
            "statementShape": shape[:1000],
            "statementSha256": hashlib.sha256(
                stripped.encode("utf-8")
            ).hexdigest(),
        }
        try:
            with _SQL_AUDIT_LOCK:
                with output_path.open("a", encoding="utf-8") as handle:
                    handle.write(
                        json.dumps(record, ensure_ascii=True, sort_keys=True)
                        + "\n"
                    )
        except OSError:
            pass

    connection.set_trace_callback(trace)


@contextmanager
def runtime_connection(
    path: Path,
    role: DatabaseRole,
    *,
    read_only: bool = False,
) -> Iterator[sqlite3.Connection]:
    identity = verify_database(path, role)
    if read_only:
        uri = f"{path.resolve().as_uri()}?mode=ro"
        connection = sqlite3.connect(uri, uri=True)
    else:
        connection = sqlite3.connect(path)
    connection.row_factory = sqlite3.Row
    _configure(connection, read_only=read_only)
    connection.set_authorizer(_authorizer(role))
    _install_sql_audit(connection, role=role, database_path=path)
    try:
        yield connection
    finally:
        connection.close()


def verify_database(path: Path, role: DatabaseRole) -> DatabaseIdentity:
    path = path.resolve()
    if not path.is_file():
        raise RuntimeError(f"strict {role} database does not exist: {path}")
    connection = sqlite3.connect(path)
    try:
        _configure(connection, read_only=True)
        _verify_layout(connection, role)
        identity = _identity(connection)
        _verify_identity(identity, role)
        violations = connection.execute("PRAGMA foreign_key_check").fetchall()
        if violations:
            raise RuntimeError(
                f"strict {role} database has {len(violations)} foreign key violations"
            )
        return identity
    finally:
        connection.close()


def database_identity(path: Path, role: DatabaseRole) -> DatabaseIdentity:
    with runtime_connection(path, role, read_only=True) as connection:
        return _identity(connection)


def audit_event_hash(
    *,
    previous_event_hash: str | None,
    operation_id: str,
    actor_id: str,
    action: str,
    resource_type: str,
    resource_id: str,
    summary_json: str,
    created_at: str,
) -> str:
    return sha256_text(
        "|".join(
            (
                previous_event_hash or "",
                operation_id,
                actor_id,
                action,
                resource_type,
                resource_id,
                summary_json,
                created_at,
            )
        )
    )

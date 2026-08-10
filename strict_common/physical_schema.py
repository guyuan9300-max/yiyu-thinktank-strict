from __future__ import annotations

import hashlib
import sqlite3
from typing import Any, Iterable


def _quote(identifier: str) -> str:
    return '"' + identifier.replace('"', '""') + '"'


def tables_from_manifest(raw: dict[str, Any]) -> list[dict[str, Any]]:
    tables = raw.get("allowedTables")
    if not isinstance(tables, list):
        raise RuntimeError("physical schema manifest has no allowedTables list")
    return [dict(table) for table in tables]


def ddl_from_manifest(raw: dict[str, Any]) -> str:
    statements: list[str] = []
    for table in tables_from_manifest(raw):
        definitions: list[str] = []
        for field in table.get("fields", []):
            item = f'{_quote(str(field["name"]))} {field["type"]}'
            if bool(field.get("primary_key")):
                item += " PRIMARY KEY"
            elif not bool(field.get("nullable", True)):
                item += " NOT NULL"
            if field.get("default") is not None:
                item += " DEFAULT " + str(field["default"])
            definitions.append(item)
        for field in table.get("fields", []):
            reference = field.get("reference") or {}
            if reference.get("kind") != "foreign_key":
                continue
            definitions.append(
                "FOREIGN KEY ({source}) REFERENCES {target}({target_field}) "
                "ON DELETE {on_delete}".format(
                    source=_quote(str(field["name"])),
                    target=_quote(str(reference["target_table"])),
                    target_field=_quote(str(reference["target_field"])),
                    on_delete=str(reference.get("on_delete") or "RESTRICT"),
                )
            )
        for composite in table.get("composite_foreign_keys", []):
            source = ", ".join(_quote(str(name)) for name in composite["source_fields"])
            target = ", ".join(_quote(str(name)) for name in composite["target_fields"])
            definitions.append(
                f"FOREIGN KEY ({source}) REFERENCES "
                f"{_quote(str(composite['target_table']))}({target}) "
                f"ON DELETE {composite.get('on_delete') or 'RESTRICT'}"
            )
        for check in table.get("check_constraints", []):
            definitions.append(
                f"CONSTRAINT {_quote(str(check['name']))} "
                f"CHECK ({check['expression']})"
            )
        statements.append(
            f"CREATE TABLE {_quote(str(table['name']))} (\n  "
            + ",\n  ".join(definitions)
            + "\n) STRICT;"
        )
    for table in tables_from_manifest(raw):
        for unique in table.get("unique_constraints", []):
            columns = ", ".join(_quote(str(name)) for name in unique["fields"])
            statement = (
                f"CREATE UNIQUE INDEX {_quote(str(unique['name']))} ON "
                f"{_quote(str(table['name']))} ({columns})"
            )
            if unique.get("where"):
                statement += " WHERE " + str(unique["where"])
            statements.append(statement + ";")
    return "\n\n".join(statements) + "\n"


def ddl_sha256(raw: dict[str, Any]) -> str:
    return hashlib.sha256(ddl_from_manifest(raw).encode("utf-8")).hexdigest()


def user_tables(connection: sqlite3.Connection) -> set[str]:
    return {
        str(row[0])
        for row in connection.execute(
            "SELECT name FROM sqlite_schema "
            "WHERE type='table' AND name NOT LIKE 'sqlite_%'"
        )
    }


def normalized_structure(connection: sqlite3.Connection) -> dict[str, Any]:
    tables: list[dict[str, Any]] = []
    for table_name in sorted(user_tables(connection)):
        columns = [
            {
                "name": str(row[1]),
                "type": str(row[2]),
                "notnull": int(row[3]),
                "default": row[4],
                "pk": int(row[5]),
            }
            for row in connection.execute(f"PRAGMA table_info({_quote(table_name)})")
        ]
        foreign_keys = [
            {
                "id": int(row[0]),
                "seq": int(row[1]),
                "target_table": str(row[2]),
                "source_field": str(row[3]),
                "target_field": str(row[4]),
                "on_update": str(row[5]),
                "on_delete": str(row[6]),
            }
            for row in connection.execute(
                f"PRAGMA foreign_key_list({_quote(table_name)})"
            )
        ]
        indexes: list[dict[str, Any]] = []
        for row in connection.execute(f"PRAGMA index_list({_quote(table_name)})"):
            index_name = str(row[1])
            indexes.append(
                {
                    "name": index_name,
                    "unique": int(row[2]),
                    "origin": str(row[3]),
                    "partial": int(row[4]),
                    "fields": [
                        str(item[2])
                        for item in connection.execute(
                            f"PRAGMA index_info({_quote(index_name)})"
                        )
                    ],
                    "sql": connection.execute(
                        "SELECT sql FROM sqlite_schema WHERE type='index' AND name=?",
                        (index_name,),
                    ).fetchone()[0],
                }
            )
        create_sql = connection.execute(
            "SELECT sql FROM sqlite_schema WHERE type='table' AND name=?",
            (table_name,),
        ).fetchone()[0]
        tables.append(
            {
                "name": table_name,
                "columns": columns,
                "foreign_keys": foreign_keys,
                "indexes": sorted(indexes, key=lambda item: item["name"]),
                "sql": str(create_sql),
            }
        )
    return {"table_count": len(tables), "tables": tables}


def structure_sha256(structure: dict[str, Any]) -> str:
    import json

    encoded = json.dumps(
        structure,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def iter_foreign_key_edges(structure: dict[str, Any]) -> Iterable[tuple[str, str, str, str]]:
    for table in structure["tables"]:
        for foreign_key in table["foreign_keys"]:
            yield (
                str(table["name"]),
                str(foreign_key["source_field"]),
                str(foreign_key["target_table"]),
                str(foreign_key["target_field"]),
            )

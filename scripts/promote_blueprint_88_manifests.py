from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[1]
CONTRACTS_DIR = PROJECT_ROOT / "contracts"


def _canonical(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def _field(
    name: str,
    *,
    nullable: bool = True,
    reference: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "name": name,
        "type": "TEXT",
        "nullable": nullable,
        "default": None,
        "primary_key": False,
        "reference": reference,
    }


def _fk(target_table: str) -> dict[str, str]:
    return {
        "kind": "foreign_key",
        "target_table": target_table,
        "target_field": "id",
        "on_delete": "RESTRICT",
        "source": "v6底座迁移必需",
    }


def _insert_fields(table: dict[str, Any], after: str, additions: list[dict[str, Any]]) -> None:
    fields = table["fields"]
    existing = {str(field["name"]) for field in fields}
    additions = [field for field in additions if field["name"] not in existing]
    if not additions:
        return
    index = next(i for i, field in enumerate(fields) if field["name"] == after) + 1
    fields[index:index] = additions


def _adjust_tables(tables: list[dict[str, Any]]) -> None:
    by_name = {str(table["name"]): table for table in tables}
    schema_versions = by_name["schema_versions"]
    _insert_fields(
        schema_versions,
        "origin_instance_id",
        [_field("database_generation_id", nullable=False)],
    )

    sandboxes = by_name["sandboxes"]
    sandboxes["role"] = "row_authority"
    sandboxes["authority_rule"] = (
        "逐行权威：device/sandbox/binding 由本机权威；server_session 由组织云权威；"
        "local_session_snapshot 是本机安全投影。任何一行都不得同时由两端裁决。"
    )
    _insert_fields(
        sandboxes,
        "scope_id",
        [
            _field("principal_id", reference=_fk("principals")),
            _field("membership_id", reference=_fk("organization_memberships")),
            _field("cloud_api_url"),
            _field("secret_reference"),
            _field("secret_fingerprint"),
            _field("access_secret_hash"),
            _field("refresh_secret_hash"),
            _field("access_expires_at"),
            _field("refresh_expires_at"),
            _field("last_seen_at"),
        ],
    )
    _insert_fields(
        sandboxes,
        "deleted_at",
        [
            _field("authority_role", nullable=False),
            _field("origin_instance_id"),
        ],
    )
    for check in sandboxes.get("check_constraints", []):
        if check.get("name") == "ck_record_kind_domain":
            check["expression"] = (
                "record_kind IN ('device','sandbox','binding',"
                "'local_session_snapshot','server_session')"
            )
    sandboxes.setdefault("command_invariants", []).append(
        "server_session 必须具有 principal_id、membership_id、access_secret_hash、"
        "refresh_secret_hash 和两类到期时间；本机不得写 server_session 权威行。"
    )

    by_name["commands"]["deletion_policy"] = {
        "mode": "append_only_retention_purge",
        "rule": (
            "命令核心信封写入后不可变；仅允许以 CAS 更新 status/settled_at。"
            "普通业务接口禁止删除，达到保留期限且 legal hold 解除后，只能由 "
            "purge_ledger 证明受控物理清除。"
        ),
        "foreign_keys": "RESTRICT",
    }
    by_name["outbox_events"]["deletion_policy"] = {
        "mode": "append_only_retention_purge",
        "rule": (
            "事件正文和聚合身份写入后不可变；仅允许以 CAS 更新 status/published_at。"
            "普通业务接口禁止删除，达到保留期限且 legal hold 解除后，只能由 "
            "purge_ledger 证明受控物理清除。"
        ),
        "foreign_keys": "RESTRICT",
    }

    source_sets = by_name["source_sets"]
    _insert_fields(
        source_sets,
        "scope_id",
        [_field("client_id", reference=_fk("clients"))],
    )
    source_sets.setdefault("command_invariants", []).append(
        "项目资料、问答或知识加工使用的 source_set 必须写入 client_id；非项目通用集合才允许为空。"
    )

    ai_answers = by_name["ai_answers"]
    _insert_fields(
        ai_answers,
        "scope_id",
        [
            _field("client_id", reference=_fk("clients")),
            _field("bot_id", reference=_fk("bot_definitions")),
        ],
    )
    ai_answers.setdefault("command_invariants", []).append(
        "项目问答必须同时写入 client_id 与实际执行的 bot_id；不得以线程或当前界面猜归属。"
    )

    bot_definitions = by_name["bot_definitions"]
    _insert_fields(bot_definitions, "scope_id", [_field("agent_kind")])
    checks = bot_definitions.setdefault("check_constraints", [])
    owner = next(check for check in checks if check.get("name") == "ck_owner_xor")
    owner["expression"] = (
        "(agent_kind IS NOT NULL AND owner_principal_id IS NULL AND owner_membership_id IS NULL) "
        "OR (agent_kind IS NULL AND ((owner_principal_id IS NOT NULL) <> "
        "(owner_membership_id IS NOT NULL)))"
    )
    checks.append(
        {
            "name": "ck_agent_kind_domain",
            "expression": (
                "agent_kind IS NULL OR agent_kind IN "
                "('project_workspace','task_planning','meeting_minutes',"
                "'strategy_companion','intelligence_research','growth_companion')"
            ),
        }
    )
    bot_definitions.setdefault("unique_constraints", []).append(
        {
            "name": "uq_bot_definitions_builtin_agent_kind",
            "fields": ["scope_id", "agent_kind"],
            "where": "agent_kind IS NOT NULL",
        }
    )


def _legacy_names(path: Path) -> list[str]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    return sorted(str(item["name"]) for item in raw.get("allowedTables", []))


def _promote(
    *,
    side: str,
    draft_path: Path,
    previous_manifest_path: Path,
) -> dict[str, Any]:
    draft = json.loads(draft_path.read_text(encoding="utf-8"))
    tables = list(draft["tables"])
    _adjust_tables(tables)
    database_role = (
        "local_blueprint_88_authority_and_projection"
        if side == "local"
        else "cloud_blueprint_88_authority_and_projection"
    )
    source_boundary = (
        "本地仅裁决设备、sandbox、本机原件与明确本地偏好；组织事实保留同名投影行，"
        "不得反向覆盖云端权威。"
        if side == "local"
        else "组织云裁决组织身份、权限与组织业务事实；本机原件和本机偏好只保留安全投影。"
    )
    return {
        "formatVersion": 1,
        "manifestId": f"yiyu.strict.{side}.blueprint88.v1",
        "status": "FROZEN_FOR_IMPLEMENTATION",
        "contractDate": "2026-08-05",
        "schemaFamily": "yiyu-blueprint-88-v1",
        "contractVersion": "7",
        "databaseRole": database_role,
        "databaseEngine": "SQLite 3 STRICT tables",
        "requiredPragmas": {
            "foreign_keys": "ON",
            "journal_mode": "WAL",
            "synchronous": "FULL",
            "trusted_schema": "OFF",
            "busy_timeout_ms": 10000,
        },
        "commonRules": {
            "tableBoundary": "活动数据库恰好包含本 manifest 的 88 张表",
            "idStorage": "稳定不透明 TEXT；新 ID 使用 UUIDv7",
            "scopeBoundary": "业务归属只使用 scope_id；本机组织投影另外固定 sandbox_id",
            "authorityBoundary": "每一行始终只有一个权威侧；非权威侧只能保存可重建投影",
            "secretBoundary": "原始密钥只进入操作系统或服务器 secret store；SQLite 只保存引用、指纹或不可逆哈希",
            "ddlBoundary": "DDL 只允许集中建库器和离线迁移器执行；运行时 authorizer 拒绝 DDL、ATTACH 和表外访问",
            "deletionBoundary": "默认 CAS 墹碑；清除只经 purge_ledger 分层结算；外键统一 RESTRICT",
        },
        "sourceOfTruthBoundary": source_boundary,
        "forbiddenRuntime": {
            "allTablesOutsideManifest": True,
            "frozenPreviousTables": _legacy_names(previous_manifest_path),
            "genericAuthorityColumns": ["payload_json", "metadata_json"],
            "legacyFallback": ["/api/v1", "ATTACH", "projection_business_objects"],
        },
        "allowedTables": tables,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--local-draft", type=Path, required=True)
    parser.add_argument("--cloud-draft", type=Path, required=True)
    args = parser.parse_args()
    for side, draft in (("local", args.local_draft), ("cloud", args.cloud_draft)):
        manifest_path = CONTRACTS_DIR / f"strict-{side}-schema-manifest.v1.json"
        manifest = _promote(
            side=side,
            draft_path=draft,
            previous_manifest_path=manifest_path,
        )
        manifest_path.write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        digest = hashlib.sha256(_canonical(manifest).encode("utf-8")).hexdigest()
        (CONTRACTS_DIR / f"strict-{side}-schema-manifest.v1.canonical.sha256").write_text(
            digest + "\n",
            encoding="utf-8",
        )
        print(f"{side}: tables={len(manifest['allowedTables'])} manifest={digest}")


if __name__ == "__main__":
    main()

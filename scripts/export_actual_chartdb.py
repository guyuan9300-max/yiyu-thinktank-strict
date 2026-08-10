#!/usr/bin/env python3
"""Export one real SQLite database to ChartDB JSON using a reviewed layout."""

from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
import time
from pathlib import Path
from typing import Any


def _stable(prefix: str, *parts: str) -> str:
    value = "|".join(parts)
    return f"{prefix}_{hashlib.sha256(value.encode('utf-8')).hexdigest()[:16]}"


def _quote(value: str) -> str:
    return '"' + value.replace('"', '""') + '"'


def _structure_hash(connection: sqlite3.Connection) -> str:
    facts: list[dict[str, Any]] = []
    tables = [
        str(row[0])
        for row in connection.execute(
            "SELECT name FROM sqlite_schema WHERE type='table' "
            "AND name NOT LIKE 'sqlite_%' ORDER BY name"
        )
    ]
    for table in tables:
        facts.append(
            {
                "name": table,
                "columns": [list(row) for row in connection.execute(f"PRAGMA table_info({_quote(table)})")],
                "foreignKeys": [list(row) for row in connection.execute(f"PRAGMA foreign_key_list({_quote(table)})")],
                "indexes": [
                    {
                        "row": list(row),
                        "fields": [list(item) for item in connection.execute(f"PRAGMA index_info({_quote(str(row[1]))})")],
                    }
                    for row in connection.execute(f"PRAGMA index_list({_quote(table)})")
                ],
            }
        )
    encoded = json.dumps(facts, ensure_ascii=True, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def export(database: Path, template: Path, output: Path, title: str) -> dict[str, Any]:
    connection = sqlite3.connect(f"file:{database.resolve()}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA query_only=ON")
    quick_check = str(connection.execute("PRAGMA quick_check").fetchone()[0])
    fk_violations = len(connection.execute("PRAGMA foreign_key_check").fetchall())
    template_document = json.loads(template.read_text(encoding="utf-8"))
    template_tables = {str(item["name"]): item for item in template_document["tables"]}
    table_names = [
        str(row[0])
        for row in connection.execute(
            "SELECT name FROM sqlite_schema WHERE type='table' "
            "AND name NOT LIKE 'sqlite_%' ORDER BY name"
        )
    ]
    if len(table_names) != 88:
        raise RuntimeError(f"actual database must have exactly 88 tables, got {len(table_names)}")
    if set(table_names) != set(template_tables):
        raise RuntimeError("actual table names do not match the reviewed 88-table layout")

    now_ms = int(time.time() * 1000)
    tables: list[dict[str, Any]] = []
    field_ids: dict[tuple[str, str], str] = {}
    table_ids: dict[str, str] = {}
    for table_name in table_names:
        layout = template_tables[table_name]
        table_id = _stable("table", title, table_name)
        table_ids[table_name] = table_id
        single_unique: set[str] = set()
        index_rows = list(connection.execute(f"PRAGMA index_list({_quote(table_name)})"))
        for index_row in index_rows:
            if not int(index_row[2]):
                continue
            fields = [str(row[2]) for row in connection.execute(f"PRAGMA index_info({_quote(str(index_row[1]))})")]
            if len(fields) == 1:
                single_unique.add(fields[0])
        fields: list[dict[str, Any]] = []
        template_fields = {str(item["name"]): item for item in layout.get("fields", [])}
        for column in connection.execute(f"PRAGMA table_info({_quote(table_name)})"):
            field_name = str(column[1])
            field_id = _stable("field", title, table_name, field_name)
            field_ids[(table_name, field_name)] = field_id
            template_field = template_fields.get(field_name, {})
            primary = bool(column[5])
            fields.append(
                {
                    "id": field_id,
                    "name": field_name,
                    "type": {"id": str(column[2]).casefold(), "name": str(column[2])},
                    "primaryKey": primary,
                    "unique": primary or field_name in single_unique,
                    "nullable": False if primary else not bool(column[3]),
                    "default": column[4],
                    "comments": template_field.get("comments") or "来自当前实际 SQLite PRAGMA",
                    "createdAt": now_ms,
                }
            )
        indexes: list[dict[str, Any]] = []
        for index_row in index_rows:
            index_name = str(index_row[1])
            index_fields = [str(row[2]) for row in connection.execute(f"PRAGMA index_info({_quote(index_name)})")]
            if not index_fields or any((table_name, field) not in field_ids for field in index_fields):
                continue
            sql_row = connection.execute(
                "SELECT sql FROM sqlite_schema WHERE type='index' AND name=?",
                (index_name,),
            ).fetchone()
            display_name = index_name
            if sql_row and sql_row[0] and " WHERE " in str(sql_row[0]).upper():
                display_name += " WHERE " + str(sql_row[0]).split(" WHERE ", 1)[1]
            indexes.append(
                {
                    "id": _stable("index", title, table_name, index_name),
                    "name": display_name,
                    "unique": bool(index_row[2]),
                    "fieldIds": [field_ids[(table_name, field)] for field in index_fields],
                    "createdAt": now_ms,
                }
            )
        tables.append(
            {
                "id": table_id,
                "name": table_name,
                "schema": "",
                "x": layout["x"],
                "y": layout["y"],
                "width": layout.get("width", 390),
                "fields": fields,
                "indexes": indexes,
                "color": layout.get("color", "#ffffff"),
                "isView": False,
                "comments": "当前活动数据库实际结构",
                "createdAt": now_ms,
            }
        )

    relationships: list[dict[str, Any]] = []
    for table_name in table_names:
        for foreign_key in connection.execute(f"PRAGMA foreign_key_list({_quote(table_name)})"):
            target_table = str(foreign_key[2])
            source_field = str(foreign_key[3])
            target_field = str(foreign_key[4])
            if (table_name, source_field) not in field_ids or (target_table, target_field) not in field_ids:
                raise RuntimeError(f"unresolved foreign key {table_name}.{source_field}")
            on_delete = str(foreign_key[6])
            relationships.append(
                {
                    "id": _stable("relationship", title, table_name, source_field, target_table, target_field),
                    "name": f"当前实际外键：{table_name}.{source_field} → {target_table}.{target_field}｜删除 {on_delete}",
                    "sourceSchema": "",
                    "sourceTableId": table_ids[table_name],
                    "targetSchema": "",
                    "targetTableId": table_ids[target_table],
                    "sourceFieldId": field_ids[(table_name, source_field)],
                    "targetFieldId": field_ids[(target_table, target_field)],
                    "sourceCardinality": "many",
                    "targetCardinality": "one",
                    "createdAt": now_ms,
                }
            )

    identity = connection.execute(
        """
        SELECT schema_family, CAST(version AS TEXT), manifest_hash,
               database_generation_id FROM schema_versions
        WHERE status='active' ORDER BY activated_at DESC LIMIT 1
        """
    ).fetchone()
    structure_hash = _structure_hash(connection)
    connection.close()
    diagram_id = _stable("diagram", title)
    document = {
        "id": diagram_id,
        "name": title,
        "databaseType": "generic",
        "tables": tables,
        "relationships": relationships,
        "dependencies": [],
        "areas": template_document.get("areas", []),
        "customTypes": [],
        "notes": [
            {
                "id": _stable("note", title),
                "x": 0,
                "y": -980,
                "width": 3000,
                "height": 850,
                "color": "#dcfce7",
                "order": 0,
                "content": (
                    f"# {title}\n\n"
                    "**状态：从当前活动 SQLite 数据库 PRAGMA 实时导出，不是蓝图推演。**\n\n"
                    f"- 实际表数：88\n"
                    f"- 实际外键连线：{len(relationships)}\n"
                    f"- quick_check：{quick_check}\n"
                    f"- foreign_key_check 违规：{fk_violations}\n"
                    f"- schema family：{identity[0]}\n"
                    f"- contract version：{identity[1]}\n"
                    f"- manifest：{identity[2]}\n"
                    f"- database generation：{identity[3]}\n"
                    f"- 实际结构 SHA256：{structure_hash}"
                ),
                "createdAt": now_ms,
            }
        ],
        "createdAt": now_ms,
        "updatedAt": now_ms,
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(document, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return {
        "output": str(output),
        "tableCount": len(tables),
        "relationshipCount": len(relationships),
        "quickCheck": quick_check,
        "foreignKeyViolationCount": fk_violations,
        "structureSha256": structure_hash,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--database", type=Path, required=True)
    parser.add_argument("--template", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--title", required=True)
    args = parser.parse_args()
    print(json.dumps(export(args.database, args.template, args.output, args.title), ensure_ascii=False))


if __name__ == "__main__":
    main()

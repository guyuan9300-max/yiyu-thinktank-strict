from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import sharepoint2text
from openpyxl import Workbook

from backend.app.local_ocr.models import (
    package_files,
    package_manifest,
    package_manifest_hash,
)
from backend.app.material_ingestion import discover_import_paths
from backend.app.project_materials_local import LocalProjectMaterialsRepository
from strict_common.ids import utc_now


def test_folder_import_filters_system_video_temporary_and_unknown_files(
    tmp_path: Path,
) -> None:
    (tmp_path / ".DS_Store").write_bytes(b"junk")
    (tmp_path / "._报告.docx").write_bytes(b"junk")
    (tmp_path / "~$报告.docx").write_bytes(b"junk")
    (tmp_path / "宣传片.mp4").write_bytes(b"video")
    (tmp_path / "程序.dmg").write_bytes(b"binary")
    (tmp_path / "调试数据.json").write_text('{"debug": true}', encoding="utf-8")
    (tmp_path / "运行记录.jsonl").write_text('{"event": "debug"}', encoding="utf-8")
    (tmp_path / "项目说明.md").write_text("可导入", encoding="utf-8")
    (tmp_path / "会议录音.m4a").write_bytes(b"audio")
    (tmp_path / "扫描件.pdf").write_bytes(b"pdf")

    accepted, skipped = discover_import_paths([tmp_path])

    assert {path.name for path in accepted} == {
        "项目说明.md",
        "会议录音.m4a",
        "扫描件.pdf",
    }
    reasons = {item["fileName"]: item["reason"] for item in skipped}
    assert reasons[".DS_Store"] == "system_or_temporary"
    assert reasons["宣传片.mp4"] == "video_not_material"
    assert reasons["程序.dmg"] == "unsupported_format"
    assert reasons["调试数据.json"] == "unsupported_format"
    assert reasons["运行记录.jsonl"] == "unsupported_format"


def test_apple_office_package_is_one_document_not_internal_files(tmp_path: Path) -> None:
    package = tmp_path / "项目方案.pages"
    (package / "Data").mkdir(parents=True)
    (package / "index.xml").write_text("<document />", encoding="utf-8")
    (package / "Data" / "preview.jpg").write_bytes(b"preview")

    accepted, skipped = discover_import_paths([tmp_path])

    assert accepted == [package]
    assert skipped == []


def test_legacy_office_extensions_are_owned_by_the_unified_parser() -> None:
    assert sharepoint2text.is_supported_file("旧合同.doc")
    assert sharepoint2text.is_supported_file("旧台账.xls")
    assert sharepoint2text.is_supported_file("旧汇报.ppt")


def test_unified_office_parser_keeps_sheet_cells_and_structure(tmp_path: Path) -> None:
    source = tmp_path / "项目台账.xlsx"
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "任务"
    sheet.append(["项目", "负责人"])
    sheet.append(["日慈", "王强"])
    workbook.save(source)

    text = LocalProjectMaterialsRepository._sharepoint_document_text(source)

    assert "项目" in text
    assert "负责人" in text
    assert "日慈" in text
    assert "王强" in text


def test_ppocr_tiny_is_one_frozen_optional_package() -> None:
    manifest = package_manifest()
    files = package_files()
    assert manifest["packageId"] == "pp-ocrv6-tiny-onnx"
    assert manifest["runtime"] == "onnxruntime"
    assert {item.model_id for item in files} == {
        "PP-OCRv6_tiny_det_onnx",
        "PP-OCRv6_tiny_rec_onnx",
    }
    assert sum(item.size_bytes for item in files) == manifest["expectedDownloadBytes"]
    assert all(len(item.sha256) == 64 for item in files)
    assert len(package_manifest_hash()) == 64
    build_script = (
        Path(__file__).resolve().parents[1]
        / "scripts"
        / "build_backend_runtime.py"
    ).read_text(encoding="utf-8")
    assert "local_ocr\" / \"model_manifest.json" in build_script
    assert "--collect-all" in build_script


def test_recycle_bin_only_lists_existing_files_and_restores_same_local_source(
    tmp_path: Path,
) -> None:
    original = tmp_path / "可恢复资料.md"
    original.write_text("回收站恢复哨兵", encoding="utf-8")
    managed = tmp_path / "managed" / "source-recycle-可恢复资料.md"
    managed.parent.mkdir()
    managed.write_text("回收站恢复哨兵", encoding="utf-8")
    state = {
        "_localSandboxId": "sandbox-recycle",
        "documents": {},
        "recycledDocuments": {
            "cloud-recycle-document": {
                "documentId": "cloud-recycle-document",
                "localSourceId": "source-recycle",
                "localSummaryId": None,
                "cloudDocumentId": "cloud-recycle-document",
                "fileName": "可恢复资料.md",
                "title": "可恢复资料",
                "mediaType": "text/markdown",
                "byteSize": managed.stat().st_size,
                "contentHash": "hash-recycle",
                "managedPath": str(managed),
                "originalSourcePath": str(original),
                "deletedAt": utc_now(),
            }
        },
    }
    lifecycle_calls: list[tuple[str, str]] = []
    runtime = SimpleNamespace(
        database_path=tmp_path / "strict-local.db",
        local_storage_object_get=lambda **_: {
            "storage_key": str(managed),
            "media_type": "text/markdown",
            "byte_size": managed.stat().st_size if managed.exists() else 0,
            "content_hash": "hash-recycle",
            "version": 2,
        },
        local_storage_object_set_lifecycle=lambda **kwargs: lifecycle_calls.append(
            (str(kwargs["object_id"]), str(kwargs["lifecycle_state"]))
        ),
    )
    store = LocalProjectMaterialsRepository(runtime)  # type: ignore[arg-type]
    store._load_project_state = lambda _project_id: state  # type: ignore[method-assign]
    store._write_project_state = lambda _project_id, value: state.update(value) or value  # type: ignore[method-assign]
    store._managed_path = lambda value: Path(value)  # type: ignore[method-assign]
    store._deleted_source_entries = lambda _project_id: {}  # type: ignore[method-assign]

    assert [item["documentId"] for item in store.recycled_documents("project-recycle-test")] == [
        "cloud-recycle-document"
    ]

    store._ensure_local_source_asset = lambda **_: "cloud-recycle-document"  # type: ignore[method-assign]
    restored = store.restore_recycled_document(
        "project-recycle-test",
        "cloud-recycle-document",
    )
    assert restored["localSourceId"] == "source-recycle"
    assert lifecycle_calls == [("source-recycle", "active")]
    assert store.recycled_documents("project-recycle-test") == []
    assert "cloud-recycle-document" in store._load_project_state(  # noqa: SLF001
        "project-recycle-test"
    )["documents"]

    missing_entry = dict(state["documents"].pop("cloud-recycle-document"))
    missing_entry.update({"documentId": "cloud-recycle-document", "deletedAt": utc_now()})
    state["recycledDocuments"] = {"cloud-recycle-document": missing_entry}
    original.unlink()
    assert managed.is_file()
    assert store.recycled_documents("project-recycle-test") == []

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any


LOCAL_SUMMARY_MEDIA_TYPE = "application/vnd.yiyu.project-knowledge-summary+json"
LOCAL_MATERIAL_PREFIX = "local-project-materials"


def _stable_segment(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:32]


def project_storage_prefix(sandbox_id: str, project_id: str) -> str:
    return (
        f"{LOCAL_MATERIAL_PREFIX}/"
        f"{_stable_segment(sandbox_id)}/{_stable_segment(project_id)}/"
    )


def _managed_candidate(data_root: Path, storage_key: str) -> Path | None:
    candidate = (data_root / storage_key).resolve()
    managed_root = (data_root / LOCAL_MATERIAL_PREFIX).resolve()
    if managed_root not in candidate.parents:
        return None
    return candidate


def managed_source_is_available(
    data_root: Path,
    storage_key: str,
    *,
    byte_size: int,
) -> bool:
    candidate = _managed_candidate(data_root, storage_key)
    if candidate is None:
        return False
    try:
        return candidate.is_file() and candidate.stat().st_size == byte_size
    except OSError:
        return False


def read_summary_document(
    data_root: Path,
    storage_key: str,
    *,
    content_hash: str,
    byte_size: int,
) -> dict[str, Any] | None:
    candidate = _managed_candidate(data_root, storage_key)
    if candidate is None:
        return None
    try:
        raw = candidate.read_bytes()
        if len(raw) != byte_size or hashlib.sha256(raw).hexdigest() != content_hash:
            return None
        payload = json.loads(raw.decode("utf-8"))
    except (OSError, UnicodeDecodeError, ValueError):
        return None
    if (
        not isinstance(payload, dict)
        or payload.get("schema") != "yiyu.project-local-private-knowledge.v1"
        or payload.get("sourceScope") != "local_private"
    ):
        return None
    return payload

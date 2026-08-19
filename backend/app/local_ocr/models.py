from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any


MANIFEST_PATH = Path(__file__).with_name("model_manifest.json")


@dataclass(frozen=True)
class OcrModelFile:
    model_id: str
    role: str
    name: str
    size_bytes: int
    sha256: str
    primary_url: str
    mirror_url: str


def package_manifest() -> dict[str, Any]:
    return json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))


def package_manifest_hash() -> str:
    raw = json.dumps(
        package_manifest(), ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def package_files() -> tuple[OcrModelFile, ...]:
    result: list[OcrModelFile] = []
    for model in package_manifest().get("models", []):
        for item in model.get("files", []):
            result.append(
                OcrModelFile(
                    model_id=str(model["id"]),
                    role=str(model["role"]),
                    name=str(item["name"]),
                    size_bytes=int(item["sizeBytes"]),
                    sha256=str(item["sha256"]),
                    primary_url=str(item["primaryUrl"]),
                    mirror_url=str(item["mirrorUrl"]),
                )
            )
    return tuple(result)


def package_dir(model_root: Path) -> Path:
    return model_root / str(package_manifest()["packageId"])


def model_file_path(model_root: Path, item: OcrModelFile) -> Path:
    return package_dir(model_root) / item.role / item.name


def package_ready(model_root: Path, *, verify_hashes: bool = False) -> bool:
    for item in package_files():
        path = model_file_path(model_root, item)
        if not path.is_file() or path.stat().st_size != item.size_bytes:
            return False
        if verify_hashes:
            digest = hashlib.sha256(path.read_bytes()).hexdigest()
            if digest != item.sha256:
                return False
    return True


def installed_size(model_root: Path) -> int:
    return sum(
        path.stat().st_size
        for item in package_files()
        if (path := model_file_path(model_root, item)).is_file()
    )

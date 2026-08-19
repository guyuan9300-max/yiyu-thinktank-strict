"""File admission policy for project material imports.

This module decides whether a filesystem entry may become a strict-88
``source_asset``.  Parsing capability is deliberately handled later: accepted
audio or scanned documents are real material even when ASR/OCR is not ready,
whereas OS junk, videos and executable/archive payloads are not project
knowledge and must never enter the authority graph.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Iterable


TEXT_EXTENSIONS = {
    ".txt", ".md", ".markdown", ".csv", ".tsv", ".json", ".jsonl",
    ".xml", ".html", ".htm", ".mhtml", ".mht", ".yaml", ".yml",
}
OFFICE_EXTENSIONS = {
    ".doc", ".docx", ".rtf", ".odt",
    ".xls", ".xlsx", ".xlsm", ".xlsb", ".ods",
    ".ppt", ".pptx", ".pptm", ".odp",
}
APPLE_OFFICE_EXTENSIONS = {".pages", ".numbers", ".key"}
PDF_EXTENSIONS = {".pdf"}
IMAGE_EXTENSIONS = {
    ".png", ".jpg", ".jpeg", ".webp", ".tif", ".tiff", ".bmp", ".heic",
}
AUDIO_EXTENSIONS = {
    ".wav", ".mp3", ".m4a", ".aac", ".flac", ".ogg", ".opus", ".webm",
}
IMPORTABLE_EXTENSIONS = (
    TEXT_EXTENSIONS
    | OFFICE_EXTENSIONS
    | APPLE_OFFICE_EXTENSIONS
    | PDF_EXTENSIONS
    | IMAGE_EXTENSIONS
    | AUDIO_EXTENSIONS
)

VIDEO_EXTENSIONS = {
    ".mp4", ".mov", ".m4v", ".avi", ".mkv", ".flv", ".wmv", ".mpeg",
    ".mpg", ".3gp",
}
TEMP_EXTENSIONS = {
    ".tmp", ".temp", ".swp", ".swo", ".bak", ".part", ".crdownload",
    ".download",
}
IGNORED_DIRECTORY_NAMES = {
    ".git", ".svn", ".hg", ".idea", ".vscode", "node_modules",
    "__pycache__", ".venv", "venv", "dist", "build",
}
IGNORED_FILE_NAMES = {".ds_store", "thumbs.db", "desktop.ini", "icon\r"}


@dataclass(frozen=True)
class ImportDecision:
    accepted: bool
    reason: str
    category: str


def _is_hidden_or_temporary(path: Path, *, root: Path | None = None) -> bool:
    try:
        parts = path.relative_to(root).parts if root is not None else (path.name,)
    except ValueError:
        parts = (path.name,)
    lowered = [part.lower() for part in parts]
    if any(part in IGNORED_DIRECTORY_NAMES for part in lowered[:-1]):
        return True
    name = path.name
    lowered_name = name.lower()
    return (
        lowered_name in IGNORED_FILE_NAMES
        or name.startswith(".")
        or name.startswith("._")
        or name.startswith("~$")
        or name.startswith(".~lock.")
        or path.suffix.lower() in TEMP_EXTENSIONS
    )


def classify_import_path(path: Path, *, root: Path | None = None) -> ImportDecision:
    if _is_hidden_or_temporary(path, root=root):
        return ImportDecision(False, "system_or_temporary", "ignored")
    if path.is_symlink():
        return ImportDecision(False, "symbolic_link", "ignored")
    if path.is_file() and path.stat().st_size == 0:
        return ImportDecision(False, "empty_file", "ignored")
    suffix = path.suffix.lower()
    if suffix in VIDEO_EXTENSIONS:
        return ImportDecision(False, "video_not_material", "video")
    if suffix not in IMPORTABLE_EXTENSIONS:
        return ImportDecision(False, "unsupported_format", "unsupported")
    if suffix in AUDIO_EXTENSIONS:
        return ImportDecision(True, "accepted", "audio")
    if suffix in IMAGE_EXTENSIONS:
        return ImportDecision(True, "accepted", "image")
    if suffix in PDF_EXTENSIONS:
        return ImportDecision(True, "accepted", "pdf")
    if suffix in APPLE_OFFICE_EXTENSIONS:
        return ImportDecision(True, "accepted", "apple_office")
    if suffix in OFFICE_EXTENSIONS:
        return ImportDecision(True, "accepted", "office")
    return ImportDecision(True, "accepted", "text")


def discover_import_paths(raw_paths: Iterable[object]) -> tuple[list[Path], list[dict[str, str]]]:
    accepted: list[Path] = []
    skipped: list[dict[str, str]] = []
    for raw in raw_paths:
        candidate = Path(str(raw)).expanduser().resolve()
        if candidate.is_dir() and candidate.suffix.lower() not in APPLE_OFFICE_EXTENSIONS:
            root = candidate
            discovered = sorted(candidate.rglob("*"))
            apple_packages = {
                path
                for path in discovered
                if path.is_dir() and path.suffix.lower() in APPLE_OFFICE_EXTENSIONS
            }
            # ``.pages/.numbers/.key`` are package directories on macOS.  They
            # are one user document, so their internal XML/assets must never be
            # imported again as independent project material.
            entries = sorted(
                apple_packages
                | {
                    path
                    for path in discovered
                    if path.is_file()
                    and not any(package in path.parents for package in apple_packages)
                }
            )
        else:
            root = candidate.parent
            entries = [candidate]
        for path in entries:
            decision = classify_import_path(path, root=root)
            if decision.accepted:
                accepted.append(path)
            else:
                skipped.append(
                    {
                        "path": str(path),
                        "fileName": path.name,
                        "reason": decision.reason,
                        "category": decision.category,
                    }
                )
    return list(dict.fromkeys(accepted)), skipped

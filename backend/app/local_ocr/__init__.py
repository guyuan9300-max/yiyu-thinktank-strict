"""Small, platform-aware OCR adapter used by local project materials."""

from .engine import OcrUnavailableError, extract_text
from .models import package_manifest, package_manifest_hash

__all__ = [
    "OcrUnavailableError",
    "extract_text",
    "package_manifest",
    "package_manifest_hash",
]

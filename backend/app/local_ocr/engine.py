from __future__ import annotations

import json
import os
import platform
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path


class OcrUnavailableError(RuntimeError):
    pass


_IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp", ".tif", ".tiff", ".bmp", ".heic"}


def _resource_root() -> Path:
    return Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parents[3]))


def _helper_path() -> Path | None:
    explicit = os.environ.get("YIYU_VISION_OCR_HELPER", "").strip()
    candidates = [
        Path(explicit).expanduser() if explicit else None,
        _resource_root() / "local-ocr" / "yiyu-vision-ocr",
        Path(__file__).resolve().parent / "bin" / "yiyu-vision-ocr",
        Path(__file__).resolve().parents[3] / "build" / "local-ocr" / "yiyu-vision-ocr",
    ]
    for candidate in candidates:
        if candidate is not None and candidate.is_file() and os.access(candidate, os.X_OK):
            return candidate
    return None


def _run_helper(path: Path, *, max_pages: int) -> str:
    helper = _helper_path()
    if platform.system() != "Darwin" or helper is None:
        raise OcrUnavailableError("当前设备尚未配置可用 OCR；请前往系统设置配置 OCR")
    try:
        completed = subprocess.run(
            [str(helper), str(path), str(max(1, max_pages))],
            check=False,
            capture_output=True,
            text=True,
            timeout=180,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise OcrUnavailableError("本机 OCR 暂时不可用，请稍后重试") from exc
    if completed.returncode != 0:
        raise RuntimeError((completed.stderr or "本机 OCR 识别失败").strip()[:300])
    try:
        payload = json.loads(completed.stdout)
    except (TypeError, ValueError, json.JSONDecodeError) as exc:
        raise RuntimeError("本机 OCR 返回了无效结果") from exc
    return str(payload.get("text") or "").strip()


def _pptx_image_text(path: Path, *, max_pages: int) -> str:
    sections: list[str] = []
    with tempfile.TemporaryDirectory(prefix="yiyu-pptx-ocr-") as temporary:
        root = Path(temporary)
        try:
            with zipfile.ZipFile(path) as package:
                names = [
                    name for name in package.namelist()
                    if name.startswith("ppt/media/")
                    and Path(name).suffix.lower() in _IMAGE_SUFFIXES
                ][:max_pages]
                for index, name in enumerate(names, start=1):
                    target = root / f"{index}{Path(name).suffix.lower()}"
                    target.write_bytes(package.read(name))
                    text = _run_helper(target, max_pages=1)
                    if text:
                        sections.append(f"[幻灯片图片 {index}]\n{text}")
        except zipfile.BadZipFile as exc:
            raise RuntimeError("PPTX 文件结构异常，无法进行 OCR") from exc
    return "\n\n".join(sections).strip()


def extract_text(path: Path, *, max_pages: int = 80) -> str:
    suffix = path.suffix.lower()
    if suffix in {".pptx", ".pptm"}:
        return _pptx_image_text(path, max_pages=max_pages)
    if suffix == ".pdf" or suffix in _IMAGE_SUFFIXES:
        return _run_helper(path, max_pages=max_pages)
    raise RuntimeError("当前格式不适用 OCR")

from __future__ import annotations

import os
import subprocess
import shutil
import sys
from pathlib import Path

import PyInstaller.__main__


ROOT = Path(__file__).resolve().parents[1]
DIST = ROOT / "backend-dist"
WORK = ROOT / "build" / "pyinstaller"
SPEC = ROOT / "build" / "pyinstaller-spec"
OCR_BUILD = ROOT / "build" / "local-ocr"


def data_argument(source: Path, destination: str) -> str:
    return f"{source}{os.pathsep}{destination}"


def main() -> None:
    shutil.rmtree(DIST, ignore_errors=True)
    shutil.rmtree(WORK, ignore_errors=True)
    shutil.rmtree(SPEC, ignore_errors=True)
    DIST.mkdir(parents=True, exist_ok=True)
    WORK.mkdir(parents=True, exist_ok=True)
    SPEC.mkdir(parents=True, exist_ok=True)

    extra_arguments: list[str] = []
    if sys.platform == "darwin":
        OCR_BUILD.mkdir(parents=True, exist_ok=True)
        ocr_helper = OCR_BUILD / "yiyu-vision-ocr"
        subprocess.run(
            [
                "xcrun", "swiftc",
                str(ROOT / "backend" / "app" / "local_ocr" / "macos_vision.swift"),
                "-framework", "AppKit",
                "-framework", "PDFKit",
                "-framework", "Vision",
                "-o", str(ocr_helper),
            ],
            check=True,
        )
        extra_arguments.extend(
            ["--add-binary", data_argument(ocr_helper, "local-ocr")]
        )

    PyInstaller.__main__.run(
        [
            "--noconfirm",
            "--clean",
            "--onefile",
            "--name",
            "yiyu-strict-backend",
            "--distpath",
            str(DIST),
            "--workpath",
            str(WORK),
            "--specpath",
            str(SPEC),
            "--paths",
            str(ROOT),
            "--add-data",
            data_argument(ROOT / "contracts", "contracts"),
            "--add-data",
            data_argument(
                ROOT / "backend" / "app" / "local_ocr" / "model_manifest.json",
                "backend/app/local_ocr",
            ),
            "--collect-data",
            "docx",
            "--collect-all",
            "sharepoint2text",
            *extra_arguments,
            str(ROOT / "scripts" / "strict_backend_entry.py"),
        ]
    )

    suffix = ".exe" if os.name == "nt" else ""
    executable = DIST / f"yiyu-strict-backend{suffix}"
    if not executable.is_file() or executable.stat().st_size == 0:
        raise RuntimeError("严格新版后端运行时构建失败")
    print(f"Built {executable} ({executable.stat().st_size} bytes)")


if __name__ == "__main__":
    main()

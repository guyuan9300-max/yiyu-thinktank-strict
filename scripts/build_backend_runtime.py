from __future__ import annotations

import os
import shutil
from pathlib import Path

import PyInstaller.__main__


ROOT = Path(__file__).resolve().parents[1]
DIST = ROOT / "backend-dist"
WORK = ROOT / "build" / "pyinstaller"
SPEC = ROOT / "build" / "pyinstaller-spec"


def data_argument(source: Path, destination: str) -> str:
    return f"{source}{os.pathsep}{destination}"


def main() -> None:
    shutil.rmtree(DIST, ignore_errors=True)
    shutil.rmtree(WORK, ignore_errors=True)
    shutil.rmtree(SPEC, ignore_errors=True)
    DIST.mkdir(parents=True, exist_ok=True)
    WORK.mkdir(parents=True, exist_ok=True)
    SPEC.mkdir(parents=True, exist_ok=True)

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
            "--collect-data",
            "docx",
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

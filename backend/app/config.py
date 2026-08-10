from __future__ import annotations

import os
import platform
from dataclasses import dataclass
from pathlib import Path


APP_DATA_NAME = "YiyuThinkTankStrictV1"
SECRET_NAMESPACE = "com.yiyu.thinktank.strict.v1"


def _default_data_dir() -> Path:
    system = platform.system()
    home = Path.home()
    if system == "Darwin":
        return home / "Library" / "Application Support" / APP_DATA_NAME
    if system == "Windows":
        base = Path(os.environ.get("APPDATA", home / "AppData" / "Roaming"))
        return base / APP_DATA_NAME
    base = Path(os.environ.get("XDG_DATA_HOME", home / ".local" / "share"))
    return base / APP_DATA_NAME


@dataclass(frozen=True)
class LocalConfig:
    data_dir: Path
    database_path: Path
    host: str
    port: int
    desktop_token: str
    secret_namespace: str
    test_mode: bool

    @classmethod
    def load(cls, data_dir: Path | None = None) -> "LocalConfig":
        resolved = (
            data_dir
            or Path(
                os.environ.get(
                    "YIYU_STRICT_DATA_DIR",
                    str(_default_data_dir()),
                )
            )
        ).expanduser().resolve()
        desktop_token = os.environ.get("YIYU_STRICT_LOCAL_API_TOKEN", "").strip()
        if not desktop_token:
            raise RuntimeError("YIYU_STRICT_LOCAL_API_TOKEN is required")
        return cls(
            data_dir=resolved,
            database_path=resolved / "strict-local.db",
            host=os.environ.get("YIYU_STRICT_LOCAL_HOST", "127.0.0.1"),
            port=int(os.environ.get("YIYU_STRICT_LOCAL_PORT", "47929")),
            desktop_token=desktop_token,
            secret_namespace=os.environ.get(
                "YIYU_STRICT_SECRET_NAMESPACE",
                SECRET_NAMESPACE,
            ).strip(),
            test_mode=os.environ.get("YIYU_STRICT_TEST_MODE") == "1",
        )

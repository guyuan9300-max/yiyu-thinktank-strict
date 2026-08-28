from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class CloudConfig:
    data_dir: Path
    database_path: Path
    bootstrap_token: str
    master_key: str
    cloud_instance_id: str | None

    @classmethod
    def load(cls, data_dir: Path | None = None) -> "CloudConfig":
        resolved = (
            data_dir
            or Path(os.environ.get("YIYU_STRICT_CLOUD_DATA_DIR", "./tmp/strict-cloud"))
        ).resolve()
        bootstrap_token = os.environ.get(
            "YIYU_STRICT_CLOUD_BOOTSTRAP_TOKEN", ""
        ).strip()
        master_key = os.environ.get("YIYU_STRICT_CLOUD_MASTER_KEY", "").strip()
        if not bootstrap_token:
            raise RuntimeError("YIYU_STRICT_CLOUD_BOOTSTRAP_TOKEN is required")
        if not master_key:
            raise RuntimeError("YIYU_STRICT_CLOUD_MASTER_KEY is required")
        cloud_instance_id = os.environ.get(
            "YIYU_STRICT_CLOUD_INSTANCE_ID", ""
        ).strip()
        if not cloud_instance_id:
            raise RuntimeError("YIYU_STRICT_CLOUD_INSTANCE_ID is required")
        return cls(
            data_dir=resolved,
            database_path=resolved / "strict-cloud.db",
            bootstrap_token=bootstrap_token,
            master_key=master_key,
            cloud_instance_id=cloud_instance_id,
        )

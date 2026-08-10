from __future__ import annotations

import os

import uvicorn

from backend.app.config import LocalConfig
from backend.app.main import create_app


def main() -> None:
    config = LocalConfig.load()
    uvicorn.run(
        create_app(config),
        host=config.host,
        port=config.port,
        log_level=os.environ.get("YIYU_STRICT_LOG_LEVEL", "info"),
    )


if __name__ == "__main__":
    main()

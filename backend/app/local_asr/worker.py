"""Isolated local-ASR worker used to keep the desktop API responsive."""

from __future__ import annotations

import argparse
import json
from dataclasses import asdict
from pathlib import Path

from .engine import transcribe_recording


PREFIX = "YIYU_ASR:"


def _emit(payload: dict[str, object]) -> None:
    print(PREFIX + json.dumps(payload, ensure_ascii=False), flush=True)


def main() -> int:
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--model-root", required=True)
    parser.add_argument("--audio", required=True)
    parser.add_argument("--language", default="auto")
    args = parser.parse_args()
    try:
        outcome = transcribe_recording(
            Path(args.model_root),
            args.audio,
            language=args.language,
            progress_callback=lambda percent, stage: _emit(
                {"kind": "progress", "percent": percent, "stage": stage}
            ),
        )
        _emit({"kind": "result", "outcome": asdict(outcome)})
        return 0
    except Exception as exc:  # noqa: BLE001
        _emit({"kind": "error", "errorType": exc.__class__.__name__})
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

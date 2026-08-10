"""Run local ASR outside the desktop API process.

The model can be CPU intensive.  Keeping it in a child process prevents a long
transcription from starving task/meeting saves and the health endpoint.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any, Callable, Mapping


ProgressCallback = Callable[[int, str], None]


def _lower_worker_priority() -> None:
    # ASR may use several CPU cores for a long recording.  It must yield to the
    # desktop API so saving, health checks and navigation stay responsive.
    os.nice(10)


def run_local_asr_subprocess(
    *,
    model_root: Path,
    audio_path: Path,
    language: str = "auto",
    progress_callback: ProgressCallback | None = None,
) -> Mapping[str, Any]:
    process = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "backend.app.local_asr.worker",
            "--model-root",
            str(model_root),
            "--audio",
            str(audio_path),
            "--language",
            language,
        ],
        cwd=str(Path(__file__).resolve().parents[3]),
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        text=True,
        bufsize=1,
        preexec_fn=_lower_worker_priority,
    )
    outcome: Mapping[str, Any] | None = None
    worker_error = ""
    assert process.stdout is not None
    for raw_line in process.stdout:
        line = raw_line.strip()
        if not line.startswith("YIYU_ASR:"):
            continue
        try:
            message = json.loads(line[len("YIYU_ASR:") :])
        except (TypeError, ValueError, json.JSONDecodeError):
            continue
        if message.get("kind") == "progress" and progress_callback:
            progress_callback(
                int(message.get("percent") or 0),
                str(message.get("stage") or "正在转写"),
            )
        elif message.get("kind") == "result" and isinstance(
            message.get("outcome"), Mapping
        ):
            outcome = dict(message["outcome"])
        elif message.get("kind") == "error":
            worker_error = str(message.get("errorType") or "WorkerError")
    return_code = process.wait()
    if return_code != 0 or outcome is None:
        raise RuntimeError(worker_error or "LocalAsrWorkerFailed")
    return outcome

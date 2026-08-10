"""Device-local speech recognition runtime for the strict application.

The models, temporary audio, and execution results stay on the current device.
This package has no database access and never reads a legacy application data
directory.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class TranscriptionSegment:
    start_ms: int
    end_ms: int
    text: str
    speaker_id: str | None = None
    emotion: str | None = None
    event: str | None = None


@dataclass
class TranscriptionResult:
    text: str
    segments: list[TranscriptionSegment]
    language: str = ""
    duration_ms: int = 0
    elapsed_ms: float = 0.0
    model_name: str = "sense-voice-small"


@dataclass
class DiarizationSegment:
    start_ms: int
    end_ms: int
    speaker: int


@dataclass
class RecordingOutcome:
    result: TranscriptionResult
    source_format: str
    transcoded_to_wav: bool
    dialogue_text: str = ""
    num_speakers: int = 1
    diarization_used: bool = False
    diarization_error: str | None = None


def speaker_label(index: int) -> str:
    if index < 0:
        return "说话人未知"
    label = ""
    value = index
    while True:
        label = chr(ord("A") + (value % 26)) + label
        value = value // 26 - 1
        if value < 0:
            break
    return f"说话人{label}"

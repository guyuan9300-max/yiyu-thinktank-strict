from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


SENSE_VOICE_MODEL = "sherpa-onnx-sense-voice-zh-en-ja-ko-yue-2024-07-17"
SEGMENTATION_MODEL = "sherpa-onnx-pyannote-segmentation-3-0"
EMBEDDING_MODEL = "3dspeaker-speech-eres2net-base-sv-zh-cn-16k-common"


@dataclass(frozen=True)
class ModelFile:
    name: str
    role: str
    primary_url: str
    mirror_url: str


@dataclass(frozen=True)
class ModelSpec:
    name: str
    files: tuple[ModelFile, ...]


_HF_SENSE = (
    "https://huggingface.co/csukuangfj/"
    "sherpa-onnx-sense-voice-zh-en-ja-ko-yue-2024-07-17/resolve/main"
)
_MIRROR_SENSE = (
    "https://hf-mirror.com/csukuangfj/"
    "sherpa-onnx-sense-voice-zh-en-ja-ko-yue-2024-07-17/resolve/main"
)
_HF_SEGMENTATION = (
    "https://huggingface.co/csukuangfj/"
    "sherpa-onnx-pyannote-segmentation-3-0/resolve/main"
)
_MIRROR_SEGMENTATION = (
    "https://hf-mirror.com/csukuangfj/"
    "sherpa-onnx-pyannote-segmentation-3-0/resolve/main"
)
_EMBEDDING_URL = (
    "https://github.com/k2-fsa/sherpa-onnx/releases/download/"
    "speaker-recongition-models/"
    "3dspeaker_speech_eres2net_base_200k_sv_zh-cn_16k-common.onnx"
)

MODEL_SPECS: dict[str, ModelSpec] = {
    SENSE_VOICE_MODEL: ModelSpec(
        SENSE_VOICE_MODEL,
        (
            ModelFile(
                "model.int8.onnx",
                "model",
                f"{_HF_SENSE}/model.int8.onnx",
                f"{_MIRROR_SENSE}/model.int8.onnx",
            ),
            ModelFile(
                "tokens.txt",
                "tokens",
                f"{_HF_SENSE}/tokens.txt",
                f"{_MIRROR_SENSE}/tokens.txt",
            ),
        ),
    ),
    SEGMENTATION_MODEL: ModelSpec(
        SEGMENTATION_MODEL,
        (
            ModelFile(
                "model.onnx",
                "model",
                f"{_HF_SEGMENTATION}/model.onnx",
                f"{_MIRROR_SEGMENTATION}/model.onnx",
            ),
        ),
    ),
    EMBEDDING_MODEL: ModelSpec(
        EMBEDDING_MODEL,
        (ModelFile("model.onnx", "model", _EMBEDDING_URL, _EMBEDDING_URL),),
    ),
}


def model_dir(root: Path, model_name: str) -> Path:
    return root / model_name


def model_files(root: Path, model_name: str) -> dict[str, Path]:
    spec = MODEL_SPECS.get(model_name)
    if spec is None:
        return {}
    directory = model_dir(root, model_name)
    return {item.role: directory / item.name for item in spec.files}


def model_ready(root: Path, model_name: str) -> bool:
    files = model_files(root, model_name)
    return bool(files) and all(path.is_file() and path.stat().st_size > 0 for path in files.values())


def diarization_ready(root: Path) -> bool:
    return model_ready(root, SEGMENTATION_MODEL) and model_ready(root, EMBEDDING_MODEL)


def model_size(root: Path, *model_names: str) -> int:
    total = 0
    for name in model_names:
        directory = model_dir(root, name)
        if not directory.is_dir():
            continue
        for path in directory.rglob("*"):
            if path.is_file():
                try:
                    total += path.stat().st_size
                except OSError:
                    continue
    return total

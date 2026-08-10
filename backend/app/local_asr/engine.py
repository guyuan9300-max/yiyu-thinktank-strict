from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
import threading
import time
import ctypes
from pathlib import Path
from typing import Any, Callable

from . import (
    DiarizationSegment,
    RecordingOutcome,
    TranscriptionResult,
    TranscriptionSegment,
    speaker_label,
)
from .models import (
    EMBEDDING_MODEL,
    SEGMENTATION_MODEL,
    SENSE_VOICE_MODEL,
    diarization_ready,
    model_files,
    model_ready,
)


_RECOGNIZER_LOCK = threading.Lock()
_RECOGNIZERS: dict[str, Any] = {}
_DIARIZER_LOCK = threading.Lock()
_DIARIZERS: dict[str, Any] = {}
_CHUNK_MS = 30_000
ProgressCallback = Callable[[int, str], None]


def _progress(callback: ProgressCallback | None, percent: int, stage: str) -> None:
    if callback is None:
        return
    try:
        callback(max(0, min(100, int(percent))), stage)
    except Exception:
        # Progress reporting must never make transcription fail.
        return


def _load_sherpa_onnx() -> Any:
    """Preload ONNX Runtime on macOS before importing sherpa-onnx.

    The upstream macOS wheel links to a versioned ``@rpath`` dylib while the
    onnxruntime wheel stores that dylib in its own package directory. Loading it
    globally first works in both a venv and the PyInstaller extraction tree and
    avoids modifying either installation at runtime.
    """

    if os.name == "posix":
        try:
            import onnxruntime

            capi = Path(onnxruntime.__file__).resolve().parent / "capi"
            dylibs = sorted(capi.glob("libonnxruntime.*.dylib"))
            if dylibs:
                ctypes.CDLL(str(dylibs[-1]), mode=ctypes.RTLD_GLOBAL)
        except (ImportError, OSError):
            pass
    import sherpa_onnx  # type: ignore[import-untyped]

    return sherpa_onnx


def _recognizer(root: Path, *, num_threads: int = 4) -> Any:
    key = str(root.resolve())
    with _RECOGNIZER_LOCK:
        cached = _RECOGNIZERS.get(key)
        if cached is not None:
            return cached
        if not model_ready(root, SENSE_VOICE_MODEL):
            raise RuntimeError("本机 ASR 模型未就绪，请先在系统设置中下载")
        sherpa_onnx = _load_sherpa_onnx()

        files = model_files(root, SENSE_VOICE_MODEL)
        value = sherpa_onnx.OfflineRecognizer.from_sense_voice(
            model=str(files["model"]),
            tokens=str(files["tokens"]),
            num_threads=num_threads,
            use_itn=True,
            language="auto",
            debug=False,
        )
        _RECOGNIZERS[key] = value
        return value


def _read_audio(path: Path) -> tuple[Any, int]:
    import numpy as np
    import soundfile as soundfile  # type: ignore[import-untyped]

    audio, sample_rate = soundfile.read(
        str(path),
        dtype="float32",
        always_2d=False,
    )
    if audio.ndim > 1:
        audio = np.mean(audio, axis=1).astype(np.float32)
    return audio, int(sample_rate)


def _clean_tags(text: str) -> tuple[str, str, str, str]:
    language = ""
    emotion = ""
    event = ""
    remaining = text
    while remaining.startswith("<|") and "|>" in remaining:
        end = remaining.index("|>")
        token = remaining[2:end]
        remaining = remaining[end + 2 :]
        upper = token.upper()
        if token in {"zh", "en", "ja", "ko", "yue"}:
            language = language or token
        elif upper in {
            "HAPPY",
            "ANGRY",
            "SAD",
            "NEUTRAL",
            "SURPRISED",
            "FEARFUL",
            "DISGUSTED",
        }:
            emotion = emotion or upper
        elif token in {"Speech", "BGM", "Applause", "Laughter", "Cry"}:
            event = event or token
    return remaining.strip(), language, emotion, event


def _decode(recognizer: Any, sample_rate: int, audio: Any) -> tuple[str, str, str, str]:
    stream = recognizer.create_stream()
    stream.accept_waveform(sample_rate, audio)
    recognizer.decode_stream(stream)
    return _clean_tags(str(stream.result.text or "").strip())


def transcribe_audio(
    root: Path,
    audio_path: str,
    *,
    language: str = "auto",
    progress_callback: ProgressCallback | None = None,
    progress_start: int = 30,
    progress_end: int = 92,
) -> TranscriptionResult:
    source = Path(audio_path).expanduser().resolve()
    if not source.is_file():
        raise FileNotFoundError(f"音频文件不存在：{source}")
    started = time.perf_counter()
    recognizer = _recognizer(root)
    audio, sample_rate = _read_audio(source)
    _progress(progress_callback, progress_start, "读取音频")
    if sample_rate <= 0:
        raise RuntimeError("音频采样率无效")
    duration_ms = int(len(audio) * 1000 / sample_rate)
    segments: list[TranscriptionSegment] = []
    paragraphs: list[str] = []
    detected_language = ""
    chunk_starts = list(range(0, max(duration_ms, 1), _CHUNK_MS))
    for chunk_index, start_ms in enumerate(chunk_starts):
        end_ms = min(start_ms + _CHUNK_MS, duration_ms)
        start_sample = int(start_ms * sample_rate / 1000)
        end_sample = int(end_ms * sample_rate / 1000)
        chunk = audio[start_sample:end_sample]
        if len(chunk) == 0:
            continue
        text, chunk_language, emotion, event = _decode(
            recognizer,
            sample_rate,
            chunk,
        )
        detected_language = detected_language or chunk_language
        if text:
            paragraphs.append(text)
            segments.append(
                TranscriptionSegment(
                    start_ms=start_ms,
                    end_ms=end_ms,
                    text=text,
                    emotion=emotion or None,
                    event=event or None,
                )
            )
        span = max(0, progress_end - progress_start)
        _progress(
            progress_callback,
            progress_start + round(span * (chunk_index + 1) / max(1, len(chunk_starts))),
            "模型转写",
        )
    if not segments:
        segments = [TranscriptionSegment(0, duration_ms, "")]
    return TranscriptionResult(
        text="\n\n".join(paragraphs),
        segments=segments,
        language=detected_language or language,
        duration_ms=duration_ms,
        elapsed_ms=(time.perf_counter() - started) * 1000,
        model_name=SENSE_VOICE_MODEL,
    )


def _diarizer(root: Path, *, num_threads: int = 2) -> Any:
    key = str(root.resolve())
    with _DIARIZER_LOCK:
        cached = _DIARIZERS.get(key)
        if cached is not None:
            return cached
        if not diarization_ready(root):
            raise RuntimeError("说话人分离模型未就绪")
        sherpa_onnx = _load_sherpa_onnx()

        segmentation = model_files(root, SEGMENTATION_MODEL)["model"]
        embedding = model_files(root, EMBEDDING_MODEL)["model"]
        config = sherpa_onnx.OfflineSpeakerDiarizationConfig(
            segmentation=sherpa_onnx.OfflineSpeakerSegmentationModelConfig(
                pyannote=sherpa_onnx.OfflineSpeakerSegmentationPyannoteModelConfig(
                    model=str(segmentation),
                ),
                num_threads=num_threads,
            ),
            embedding=sherpa_onnx.SpeakerEmbeddingExtractorConfig(
                model=str(embedding),
                num_threads=num_threads,
            ),
            clustering=sherpa_onnx.FastClusteringConfig(
                num_clusters=-1,
                threshold=0.5,
            ),
            min_duration_on=0.3,
            min_duration_off=0.5,
        )
        value = sherpa_onnx.OfflineSpeakerDiarization(config)
        _DIARIZERS[key] = value
        return value


def _diarize(root: Path, wav_path: Path) -> list[DiarizationSegment]:
    diarizer = _diarizer(root)
    audio, sample_rate = _read_audio(wav_path)
    if sample_rate != int(diarizer.sample_rate):
        raise RuntimeError(
            f"说话人分离要求 {int(diarizer.sample_rate)}Hz，实际为 {sample_rate}Hz"
        )
    raw = diarizer.process(audio).sort_by_start_time()
    try:
        values = list(raw)
    except TypeError:
        values = [raw[index] for index in range(raw.num_segments)]
    result = [
        DiarizationSegment(
            start_ms=int(round(float(getattr(item, "start", 0.0)) * 1000)),
            end_ms=int(round(float(getattr(item, "end", 0.0)) * 1000)),
            speaker=int(getattr(item, "speaker", 0)),
        )
        for item in values
    ]
    return sorted(
        (item for item in result if item.end_ms > item.start_ms),
        key=lambda item: item.start_ms,
    )


def _ffmpeg() -> str:
    candidates = (
        os.environ.get("YIYU_FFMPEG_PATH", ""),
        shutil.which("ffmpeg") or "",
        "/opt/homebrew/bin/ffmpeg",
        "/usr/local/bin/ffmpeg",
        "/usr/bin/ffmpeg",
    )
    for candidate in candidates:
        if candidate and Path(candidate).is_file():
            return candidate
    raise RuntimeError("未找到 ffmpeg，本机录音转写需要 ffmpeg")


def _transcode(source: Path) -> Path:
    descriptor, name = tempfile.mkstemp(prefix="yiyu-strict-asr-", suffix=".wav")
    os.close(descriptor)
    target = Path(name)
    try:
        result = subprocess.run(
            [
                _ffmpeg(),
                "-y",
                "-loglevel",
                "error",
                "-i",
                str(source),
                "-ac",
                "1",
                "-ar",
                "16000",
                "-sample_fmt",
                "s16",
                str(target),
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
            timeout=600,
        )
    except Exception:
        target.unlink(missing_ok=True)
        raise
    if result.returncode != 0 or not target.is_file() or target.stat().st_size == 0:
        target.unlink(missing_ok=True)
        detail = result.stderr.decode("utf-8", errors="replace").strip().splitlines()
        raise RuntimeError(
            "ffmpeg 转码失败"
            + (f"：{detail[-1]}" if detail else f"（exit {result.returncode}）")
        )
    return target


def _transcribe_segments(
    root: Path,
    wav_path: Path,
    diarization: list[DiarizationSegment],
    *,
    language: str,
    progress_callback: ProgressCallback | None = None,
    progress_start: int = 45,
    progress_end: int = 92,
) -> TranscriptionResult:
    recognizer = _recognizer(root)
    audio, sample_rate = _read_audio(wav_path)
    started = time.perf_counter()
    output: list[TranscriptionSegment] = []
    chunks: list[str] = []
    detected_language = ""
    for item_index, item in enumerate(diarization):
        start_sample = int(item.start_ms * sample_rate / 1000)
        end_sample = int(item.end_ms * sample_rate / 1000)
        chunk = audio[start_sample:end_sample]
        if len(chunk) == 0:
            continue
        text, chunk_language, emotion, event = _decode(
            recognizer,
            sample_rate,
            chunk,
        )
        detected_language = detected_language or chunk_language
        if text:
            chunks.append(text)
            output.append(
                TranscriptionSegment(
                    start_ms=item.start_ms,
                    end_ms=item.end_ms,
                    text=text,
                    speaker_id=speaker_label(item.speaker),
                    emotion=emotion or None,
                    event=event or None,
                )
            )
        span = max(0, progress_end - progress_start)
        _progress(
            progress_callback,
            progress_start + round(span * (item_index + 1) / max(1, len(diarization))),
            "分段转写",
        )
    return TranscriptionResult(
        text="".join(chunks),
        segments=output,
        language=detected_language or language,
        duration_ms=diarization[-1].end_ms if diarization else 0,
        elapsed_ms=(time.perf_counter() - started) * 1000,
        model_name=SENSE_VOICE_MODEL,
    )


def transcribe_recording(
    root: Path,
    audio_path: str,
    *,
    language: str = "auto",
    progress_callback: ProgressCallback | None = None,
) -> RecordingOutcome:
    _progress(progress_callback, 5, "校验录音")
    source = Path(audio_path).expanduser().resolve()
    if not source.is_file():
        raise FileNotFoundError(f"录音文件不存在：{source}")
    source_format = source.suffix.lstrip(".").lower()
    if not source_format:
        raise RuntimeError("录音文件没有可识别的扩展名")
    _progress(progress_callback, 10, "转换音频")
    wav_path = _transcode(source)
    _progress(progress_callback, 25, "音频已就绪")
    try:
        if diarization_ready(root):
            try:
                _progress(progress_callback, 30, "识别说话人")
                diarization = _diarize(root, wav_path)
                _progress(progress_callback, 42, "说话人识别完成")
                if diarization:
                    result = _transcribe_segments(
                        root,
                        wav_path,
                        diarization,
                        language=language,
                        progress_callback=progress_callback,
                        progress_start=45,
                        progress_end=92,
                    )
                    dialogue = "\n".join(
                        f"{item.speaker_id}：{item.text}"
                        for item in result.segments
                        if item.text and item.speaker_id
                    )
                    _progress(progress_callback, 96, "整理转写结果")
                    return RecordingOutcome(
                        result=result,
                        source_format=source_format,
                        transcoded_to_wav=True,
                        dialogue_text=dialogue,
                        num_speakers=len(
                            {
                                item.speaker_id
                                for item in result.segments
                                if item.speaker_id
                            }
                        ),
                        diarization_used=True,
                    )
            except Exception as exc:  # noqa: BLE001
                result = transcribe_audio(
                    root,
                    str(wav_path),
                    language=language,
                    progress_callback=progress_callback,
                    progress_start=42,
                    progress_end=92,
                )
                _progress(progress_callback, 96, "整理转写结果")
                return RecordingOutcome(
                    result=result,
                    source_format=source_format,
                    transcoded_to_wav=True,
                    dialogue_text=result.text,
                    num_speakers=1 if result.text else 0,
                    diarization_used=False,
                    diarization_error=f"{exc.__class__.__name__}：{exc}",
                )
        result = transcribe_audio(
            root,
            str(wav_path),
            language=language,
            progress_callback=progress_callback,
            progress_start=30,
            progress_end=92,
        )
        _progress(progress_callback, 96, "整理转写结果")
        return RecordingOutcome(
            result=result,
            source_format=source_format,
            transcoded_to_wav=True,
            dialogue_text=result.text,
            num_speakers=1 if result.text else 0,
        )
    finally:
        wav_path.unlink(missing_ok=True)

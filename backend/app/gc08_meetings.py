"""GC-08 local recording, transcription, and meeting-minutes authority.

This module writes only the frozen 88-table objects.  Recording bytes, full
transcripts, draft minutes, and every local path stay in the local database
boundary.  ``cloud_publication_payload`` deliberately emits only the formal
minutes body and safe metadata required by the cloud authority adapter.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

from strict_common.ids import canonical_json, new_id, sha256_text, utc_now
from strict_common.schema import runtime_connection


class GC08DomainError(RuntimeError):
    def __init__(self, status_code: int, code: str, message: str):
        super().__init__(message)
        self.status_code = status_code
        self.code = code
        self.message = message


class GC08BlockedError(GC08DomainError):
    pass


class GC08RetryableError(GC08DomainError):
    pass


@dataclass(frozen=True)
class GC08LocalContext:
    scope_id: str
    sandbox_id: str
    principal_id: str
    membership_id: str
    origin_instance_id: str


TranscriptionProgressCallback = Callable[[int, str], None]
TranscriptionRunner = Callable[[Path, str, TranscriptionProgressCallback | None], Any]
MinutesRunner = Callable[[str, str], Any]


def _stable_id(prefix: str, *parts: Any) -> str:
    material = "\x1f".join(str(part) for part in parts)
    return f"{prefix}_{sha256_text(material)[:30]}"


def _atomic_write(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.{new_id()}.tmp")
    try:
        temporary.write_bytes(data)
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _attribute(value: Any, name: str, default: Any = None) -> Any:
    if isinstance(value, Mapping):
        return value.get(name, default)
    return getattr(value, name, default)


def _normalize_transcription(value: Any) -> dict[str, Any]:
    result = _attribute(value, "result", value)
    dialogue = str(
        _attribute(value, "dialogue_text", "")
        or _attribute(value, "dialogueText", "")
        or _attribute(result, "dialogue_text", "")
        or _attribute(result, "dialogueText", "")
        or ""
    ).strip()
    text = dialogue or str(_attribute(result, "text", "") or "").strip()
    try:
        duration_ms = max(0, int(_attribute(result, "duration_ms", 0) or 0))
    except (TypeError, ValueError):
        duration_ms = 0
    return {
        "text": text,
        "language": str(_attribute(result, "language", "auto") or "auto"),
        "durationMs": duration_ms,
        "modelName": str(_attribute(result, "model_name", "") or ""),
    }


def parse_minutes_model_output(value: Any) -> dict[str, Any]:
    raw = _attribute(value, "content", value)
    if isinstance(raw, Mapping):
        parsed = dict(raw)
    else:
        text = str(raw or "").strip()
        if text.startswith("```") and text.endswith("```"):
            lines = text.splitlines()
            if len(lines) >= 3:
                text = "\n".join(lines[1:-1]).strip()
        try:
            loaded = json.loads(text)
        except (TypeError, ValueError, json.JSONDecodeError) as exc:
            raise GC08RetryableError(
                502,
                "meeting_minutes_response_invalid",
                "纪要生成结果结构无效，可以重试",
            ) from exc
        parsed = dict(loaded) if isinstance(loaded, Mapping) else {}
    minutes = str(
        parsed.get("minutesMarkdown")
        or parsed.get("minutesMd")
        or parsed.get("content")
        or ""
    ).strip()
    if not minutes:
        raise GC08RetryableError(
            502,
            "meeting_minutes_empty_result",
            "纪要生成未返回正文，可以重试",
        )
    citations = parsed.get("citations")
    raw_candidates = parsed.get("actionCandidates")
    action_candidates: list[dict[str, str]] = []
    if isinstance(raw_candidates, list):
        for item in raw_candidates[:20]:
            if not isinstance(item, Mapping):
                continue
            candidate_title = str(item.get("title") or "").strip()
            if not candidate_title:
                continue
            action_candidates.append(
                {
                    "title": candidate_title[:240],
                    "description": str(item.get("description") or "").strip()[:2000],
                    "dueDate": str(item.get("dueDate") or "").strip()[:10],
                    "ownerHint": str(item.get("ownerHint") or "").strip()[:120],
                }
            )
    return {
        "title": str(parsed.get("title") or "").strip(),
        "minutesMarkdown": minutes,
        "citations": citations if isinstance(citations, list) else [],
        "actionCandidates": action_candidates,
    }


class GC08LocalMeetingRepository:
    TRANSCRIPT_PROCESSOR = "local_audio_transcription"
    MINUTES_PROCESSOR = "meeting_minutes_draft"

    def __init__(
        self,
        database_path: Path,
        recordings_root: Path,
        context_provider: Callable[[], GC08LocalContext],
        *,
        transcription_runner: TranscriptionRunner | None = None,
        minutes_runner: MinutesRunner | None = None,
    ) -> None:
        self.database_path = database_path.resolve()
        self.recordings_root = recordings_root.resolve()
        self.context_provider = context_provider
        self.transcription_runner = transcription_runner
        self.minutes_runner = minutes_runner

    def _context(self) -> GC08LocalContext:
        context = self.context_provider()
        if not all(
            (
                context.scope_id,
                context.sandbox_id,
                context.principal_id,
                context.membership_id,
                context.origin_instance_id,
            )
        ):
            raise GC08DomainError(
                409,
                "gc08_workspace_context_incomplete",
                "当前工作空间身份不完整，请重新登录后重试",
            )
        return context

    def _managed_path(self, value: Path) -> Path:
        path = value.expanduser().resolve()
        if path == self.recordings_root or self.recordings_root not in path.parents:
            raise GC08DomainError(
                403,
                "recording_path_outside_strict_data",
                "只允许读取严格新版录音目录中的本机文件",
            )
        return path

    @staticmethod
    def _file_sha256(path: Path) -> str:
        digest = hashlib.sha256()
        with path.open("rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(chunk)
        return digest.hexdigest()

    def _reconcile_manifest_path(
        self,
        *,
        context: GC08LocalContext,
        manifest_id: str | None,
        content_hash: str | None,
        byte_size: int | None,
        current_path: str | None,
        availability_state: str | None,
        search_root: Path,
    ) -> str | None:
        """Follow a Finder rename without treating the filename as identity.

        The search never leaves the strict managed recording directory and only
        updates the existing manifest when exactly one file has the same size and
        SHA-256.  Moving a file elsewhere therefore remains an explicit local
        availability failure instead of becoming an arbitrary filesystem scan.
        """
        if current_path:
            try:
                managed = self._managed_path(Path(current_path))
            except GC08DomainError:
                managed = None
            if managed is not None and managed.is_file():
                if availability_state != "ready" and manifest_id:
                    with runtime_connection(self.database_path, "local") as connection:
                        connection.execute(
                            "UPDATE object_manifests SET availability_state='ready',"
                            "verified_at=? WHERE scope_id=? AND id=?",
                            (utc_now(), context.scope_id, manifest_id),
                        )
                        connection.commit()
                return str(managed)
        if not manifest_id or not content_hash or byte_size is None:
            return None
        root = search_root.resolve()
        if current_path:
            previous_parent = Path(current_path).expanduser().resolve().parent
            if previous_parent.is_dir() and (
                previous_parent == self.recordings_root
                or self.recordings_root in previous_parent.parents
            ):
                root = previous_parent
        if not root.is_dir() or (root != self.recordings_root and self.recordings_root not in root.parents):
            return None
        matches: list[Path] = []
        for candidate in root.iterdir():
            if not candidate.is_file() or candidate.name.startswith("."):
                continue
            try:
                if candidate.stat().st_size != int(byte_size):
                    continue
                if self._file_sha256(candidate) == str(content_hash):
                    matches.append(candidate.resolve())
            except OSError:
                continue
        if len(matches) != 1:
            if manifest_id and availability_state != "missing":
                with runtime_connection(self.database_path, "local") as connection:
                    connection.execute(
                        "UPDATE object_manifests SET availability_state='missing',"
                        "verified_at=? WHERE scope_id=? AND id=?",
                        (utc_now(), context.scope_id, manifest_id),
                    )
                    connection.commit()
            return None
        recovered = matches[0]
        with runtime_connection(self.database_path, "local") as connection:
            cursor = connection.execute(
                "UPDATE object_manifests SET local_original_path=?,storage_key=?,"
                "availability_state='ready',verified_at=? "
                "WHERE scope_id=? AND id=? AND content_hash=? "
                "AND lifecycle_state='active'",
                (
                    str(recovered),
                    recovered.relative_to(self.recordings_root).as_posix(),
                    utc_now(),
                    context.scope_id,
                    manifest_id,
                    content_hash,
                ),
            )
            connection.commit()
        return str(recovered) if cursor.rowcount == 1 else None

    @staticmethod
    def _require_meeting(
        connection: Any,
        context: GC08LocalContext,
        *,
        client_id: str,
        meeting_id: str,
    ) -> Mapping[str, Any]:
        row = connection.execute(
            "SELECT * FROM meetings WHERE id=? AND scope_id=? AND client_id=? "
            "AND lifecycle_state='active'",
            (meeting_id, context.scope_id, client_id),
        ).fetchone()
        if row is None:
            raise GC08DomainError(
                404,
                "meeting_client_binding_missing",
                "当前项目中不存在该会议，无法登记录音",
            )
        return row

    @staticmethod
    def _latest_attempt(
        connection: Any,
        context: GC08LocalContext,
        *,
        recording_id: str,
        processor_kind: str,
    ) -> Mapping[str, Any] | None:
        row = connection.execute(
            "SELECT * FROM processing_attempts WHERE scope_id=? "
            "AND recording_id=? AND processor_kind=? "
            "ORDER BY attempt_no DESC, started_at DESC, id DESC LIMIT 1",
            (context.scope_id, recording_id, processor_kind),
        ).fetchone()
        return dict(row) if row is not None else None

    @staticmethod
    def _start_attempt(
        connection: Any,
        context: GC08LocalContext,
        *,
        recording_id: str,
        processor_kind: str,
    ) -> tuple[str, int]:
        current = GC08LocalMeetingRepository._latest_attempt(
            connection,
            context,
            recording_id=recording_id,
            processor_kind=processor_kind,
        )
        attempt_no = int((current or {}).get("attempt_no") or 0) + 1
        attempt_id = new_id()
        connection.execute(
            "INSERT INTO processing_attempts (id,scope_id,operation_id,"
            "source_asset_id,recording_id,attempt_no,status,error_code,"
            "processor_kind,provider_resource_id,error_message_safe,next_retry_at,"
            "started_at,finished_at,authority_role,origin_instance_id) "
            "VALUES (?,?,NULL,NULL,?,?,'processing',NULL,?,NULL,NULL,NULL,?,NULL,"
            "'local',?)",
            (
                attempt_id,
                context.scope_id,
                recording_id,
                attempt_no,
                processor_kind,
                utc_now(),
                context.origin_instance_id,
            ),
        )
        connection.commit()
        return attempt_id, attempt_no

    @staticmethod
    def _finish_attempt(
        connection: Any,
        *,
        attempt_id: str,
        status: str,
        error_code: str | None = None,
        message: str | None = None,
        retryable: bool = False,
    ) -> None:
        now = utc_now()
        connection.execute(
            "UPDATE processing_attempts SET status=?,error_code=?,"
            "error_message_safe=?,next_retry_at=?,finished_at=? WHERE id=?",
            (
                status,
                error_code,
                message,
                now if retryable else None,
                now,
                attempt_id,
            ),
        )

    def register_recording(
        self,
        *,
        client_id: str,
        meeting_id: str,
        audio_path: Path,
        original_file_name: str | None = None,
        recording_id: str | None = None,
        duration_ms: int | None = None,
        captured_at: str | None = None,
        device_id: str | None = None,
    ) -> dict[str, Any]:
        context = self._context()
        source = self._managed_path(audio_path)
        if not source.is_file():
            raise GC08DomainError(404, "recording_file_missing", "本机录音文件不存在")
        data = source.read_bytes()
        if not data:
            raise GC08DomainError(422, "recording_file_empty", "录音文件为空")
        content_hash = hashlib.sha256(data).hexdigest()
        normalized_recording_id = recording_id or _stable_id(
            "recording",
            context.scope_id,
            meeting_id,
            content_hash,
        )
        manifest_id = _stable_id(
            "manifest_recording",
            context.scope_id,
            normalized_recording_id,
        )
        now = utc_now()
        with runtime_connection(self.database_path, "local") as connection:
            self._require_meeting(
                connection,
                context,
                client_id=client_id,
                meeting_id=meeting_id,
            )
            existing = connection.execute(
                "SELECT r.*,m.content_hash,m.local_original_path "
                "FROM recordings r LEFT JOIN object_manifests m "
                "ON m.scope_id=r.scope_id AND m.id=r.object_manifest_id "
                "WHERE r.scope_id=? AND r.id=?",
                (context.scope_id, normalized_recording_id),
            ).fetchone()
            if existing is not None:
                if (
                    str(existing["meeting_id"]) != meeting_id
                    or str(existing["content_hash"] or "") != content_hash
                ):
                    raise GC08DomainError(
                        409,
                        "recording_identity_conflict",
                        "录音标识已绑定其他媒体内容",
                    )
                return self.recording_detail(
                    client_id=client_id,
                    meeting_id=meeting_id,
                    recording_id=normalized_recording_id,
                )
            receipt = canonical_json(
                {
                    "boundary": "member_device_local_original",
                    "contentUploaded": False,
                    "localPathUploaded": False,
                    "originalFileName": str(original_file_name or source.name),
                }
            )
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                "INSERT INTO object_manifests (id,scope_id,storage_key,content_hash,"
                "lifecycle_state,receipt,holder_role,holder_instance_id,storage_kind,"
                "byte_size,media_type,availability_state,receipt_hash,created_at,"
                "verified_at,deleted_at,authority_role,origin_instance_id,"
                "local_original_path) VALUES (?,?,?,?,'active',?,'member_device',?,"
                "'local_recording',?,'audio/' || ?,'ready',?,?,?,NULL,'local',?,?)",
                (
                    manifest_id,
                    context.scope_id,
                    source.relative_to(self.recordings_root).as_posix(),
                    content_hash,
                    receipt,
                    context.sandbox_id,
                    len(data),
                    source.suffix.casefold().lstrip(".") or "octet-stream",
                    sha256_text(receipt),
                    now,
                    now,
                    context.origin_instance_id,
                    str(source),
                ),
            )
            connection.execute(
                "INSERT INTO secured_resources (id,scope_id,resource_kind,"
                "lifecycle_state,version,resource_type_key,created_at,updated_at,"
                "deleted_at,authority_role,origin_instance_id) VALUES (?,?,"
                "'recording','active',1,'meeting_recording',?,?,NULL,'local',?)",
                (
                    normalized_recording_id,
                    context.scope_id,
                    now,
                    now,
                    context.origin_instance_id,
                ),
            )
            connection.execute(
                "INSERT INTO recordings (id,scope_id,meeting_id,object_manifest_id,"
                "lifecycle_state,current_transcription_version_id,recording_state,"
                "duration_ms,captured_at,device_id,version,created_at,updated_at,"
                "deleted_at) VALUES (?,?,?,?,'active',NULL,'captured',?,?,?,1,?,?,NULL)",
                (
                    normalized_recording_id,
                    context.scope_id,
                    meeting_id,
                    manifest_id,
                    max(0, int(duration_ms or 0)) or None,
                    captured_at or now,
                    device_id or context.sandbox_id,
                    now,
                    now,
                ),
            )
            connection.commit()
        return self.recording_detail(
            client_id=client_id,
            meeting_id=meeting_id,
            recording_id=normalized_recording_id,
        )

    def _recording_source(
        self,
        *,
        client_id: str,
        meeting_id: str,
        recording_id: str,
    ) -> tuple[Mapping[str, Any], Path]:
        context = self._context()
        with runtime_connection(self.database_path, "local", read_only=True) as connection:
            self._require_meeting(
                connection,
                context,
                client_id=client_id,
                meeting_id=meeting_id,
            )
            row = connection.execute(
                "SELECT r.*,m.id AS manifest_id,m.local_original_path,"
                "m.content_hash,m.byte_size,m.availability_state,m.receipt "
                "FROM recordings r JOIN object_manifests m "
                "ON m.scope_id=r.scope_id AND m.id=r.object_manifest_id "
                "WHERE r.scope_id=? AND r.id=? AND r.meeting_id=? "
                "AND r.lifecycle_state='active'",
                (context.scope_id, recording_id, meeting_id),
            ).fetchone()
        if row is None:
            raise GC08DomainError(404, "recording_missing", "当前会议没有该录音")
        recovered_path = self._reconcile_manifest_path(
            context=context,
            manifest_id=row["manifest_id"],
            content_hash=row["content_hash"],
            byte_size=row["byte_size"],
            current_path=row["local_original_path"],
            availability_state=row["availability_state"],
            search_root=self.recordings_root,
        )
        if not recovered_path:
            raise GC08BlockedError(424, "recording_file_missing", "本机录音原件已不可用")
        source = self._managed_path(Path(recovered_path))
        if (
            source.stat().st_size != int(row["byte_size"] or 0)
            or self._file_sha256(source) != str(row["content_hash"] or "")
        ):
            raise GC08BlockedError(
                409,
                "recording_integrity_failed",
                "本机录音原件校验失败，已停止转写",
            )
        return dict(row), source

    def transcribe(
        self,
        *,
        client_id: str,
        meeting_id: str,
        recording_id: str,
        language: str = "auto",
        force: bool = False,
        progress_callback: TranscriptionProgressCallback | None = None,
    ) -> dict[str, Any]:
        def report(percent: int, stage: str) -> None:
            if progress_callback is None:
                return
            try:
                progress_callback(percent, stage)
            except Exception:
                return

        report(3, "准备转写")
        context = self._context()
        recording, source = self._recording_source(
            client_id=client_id,
            meeting_id=meeting_id,
            recording_id=recording_id,
        )
        with runtime_connection(self.database_path, "local") as connection:
            current = self._latest_attempt(
                connection,
                context,
                recording_id=recording_id,
                processor_kind=self.TRANSCRIPT_PROCESSOR,
            )
            if current is not None and not force and str(current["status"]) in {
                "ready",
                "blocked",
                "failed_retryable",
            }:
                return self.recording_detail(
                    client_id=client_id,
                    meeting_id=meeting_id,
                    recording_id=recording_id,
                )
            attempt_id, _ = self._start_attempt(
                connection,
                context,
                recording_id=recording_id,
                processor_kind=self.TRANSCRIPT_PROCESSOR,
            )
        if self.transcription_runner is None:
            error: GC08DomainError = GC08BlockedError(
                424,
                "local_asr_not_connected",
                "本机 ASR 未接通；录音已保留，可在能力就绪后重试",
            )
            outcome = None
        else:
            try:
                outcome = _normalize_transcription(
                    self.transcription_runner(source, language, progress_callback)
                )
                if not outcome["text"]:
                    raise GC08RetryableError(
                        502,
                        "local_asr_empty_result",
                        "ASR 未返回转写正文，录音已保留，可以重试",
                    )
                error = None  # type: ignore[assignment]
            except GC08DomainError as exc:
                error = exc
                outcome = None
            except Exception as exc:  # noqa: BLE001
                error = GC08RetryableError(
                    503,
                    "local_asr_execution_failed",
                    f"本机 ASR 暂时失败：{exc.__class__.__name__}",
                )
                outcome = None
        if error is not None:
            retryable = isinstance(error, GC08RetryableError)
            with runtime_connection(self.database_path, "local") as connection:
                self._finish_attempt(
                    connection,
                    attempt_id=attempt_id,
                    status="failed_retryable" if retryable else "blocked",
                    error_code=error.code,
                    message=error.message,
                    retryable=retryable,
                )
                connection.commit()
            return self.recording_detail(
                client_id=client_id,
                meeting_id=meeting_id,
                recording_id=recording_id,
            )

        assert outcome is not None
        report(97, "保存转写文件")
        text = str(outcome["text"])
        content_hash = sha256_text(text)
        now = utc_now()
        with runtime_connection(self.database_path, "local") as connection:
            previous = connection.execute(
                "SELECT * FROM transcription_versions WHERE scope_id=? "
                "AND recording_id=? ORDER BY version DESC LIMIT 1",
                (context.scope_id, recording_id),
            ).fetchone()
            version = int(previous["version"] or 0) + 1 if previous is not None else 1
            transcription_id = _stable_id(
                "transcription",
                context.scope_id,
                recording_id,
                version,
                content_hash,
            )
            manifest_id = _stable_id("manifest_transcription", transcription_id)
            try:
                recording_receipt = json.loads(str(recording.get("receipt") or "{}"))
            except (TypeError, ValueError, json.JSONDecodeError):
                recording_receipt = {}
            original_file_name = str(
                _mapping(recording_receipt).get("originalFileName") or source.name
            )
            transcript_stem = re.sub(
                r"[^0-9A-Za-z\u4e00-\u9fff._-]+",
                "_",
                Path(original_file_name).stem,
            ).strip("._")[:140] or "录音"
            target = (
                self.recordings_root
                / "transcripts"
                / sha256_text(recording_id)[:24]
                / f"v{version}"
                / f"{transcript_stem}-录音转写.txt"
            )
            _atomic_write(target, text.encode("utf-8"))
            receipt = canonical_json(
                {
                    "boundary": "member_device_full_transcript",
                    "contentUploaded": False,
                    "localPathUploaded": False,
                    "language": outcome["language"],
                }
            )
            try:
                connection.execute("BEGIN IMMEDIATE")
                connection.execute(
                    "INSERT INTO object_manifests (id,scope_id,storage_key,"
                    "content_hash,lifecycle_state,receipt,holder_role,"
                    "holder_instance_id,storage_kind,byte_size,media_type,"
                    "availability_state,receipt_hash,created_at,verified_at,"
                    "deleted_at,authority_role,origin_instance_id,"
                    "local_original_path) VALUES (?,?,?,?,'active',?,"
                    "'member_device',?,'local_full_transcript',?,'text/plain',"
                    "'ready',?,?,?,NULL,'local',?,?)",
                    (
                        manifest_id,
                        context.scope_id,
                        target.relative_to(self.recordings_root).as_posix(),
                        content_hash,
                        receipt,
                        context.sandbox_id,
                        len(text.encode("utf-8")),
                        sha256_text(receipt),
                        now,
                        now,
                        context.origin_instance_id,
                        str(target),
                    ),
                )
                connection.execute(
                    "INSERT INTO transcription_versions (id,scope_id,recording_id,"
                    "document_id,version,status,object_manifest_id,"
                    "provider_resource_id,language,created_at,supersedes_version_id,"
                    "origin_instance_id,integrity_hash) VALUES (?,?,?,NULL,?,'ready',"
                    "?,NULL,?,?,?,?,?)",
                    (
                        transcription_id,
                        context.scope_id,
                        recording_id,
                        version,
                        manifest_id,
                        outcome["language"],
                        now,
                        str(previous["id"]) if previous is not None else None,
                        context.origin_instance_id,
                        content_hash,
                    ),
                )
                connection.execute(
                    "UPDATE recordings SET current_transcription_version_id=?,"
                    "recording_state='transcribed',duration_ms=COALESCE(NULLIF(?,0),"
                    "duration_ms),version=version+1,updated_at=? WHERE id=? "
                    "AND scope_id=?",
                    (
                        transcription_id,
                        int(outcome["durationMs"] or 0),
                        now,
                        recording_id,
                        context.scope_id,
                    ),
                )
                self._finish_attempt(
                    connection,
                    attempt_id=attempt_id,
                    status="ready",
                )
                connection.commit()
            except Exception:
                connection.rollback()
                target.unlink(missing_ok=True)
                raise
        report(98, "转写文件已保存")
        return self.recording_detail(
            client_id=client_id,
            meeting_id=meeting_id,
            recording_id=recording_id,
        )

    @staticmethod
    def _normalize_citations(
        citations: Sequence[Mapping[str, Any]],
        *,
        transcript_characters: int,
    ) -> list[dict[str, Any]]:
        normalized: list[dict[str, Any]] = []
        raw = list(citations) or [
            {
                "locatorKind": "char_range",
                "locator": f"char:0-{transcript_characters}",
            }
        ]
        for item in raw:
            locator = str(item.get("locator") or "").strip()
            locator_kind = str(item.get("locatorKind") or "char_range").strip()
            if not locator or locator_kind not in {
                "char_range",
                "paragraph",
                "segment",
                "time_range",
            }:
                raise GC08DomainError(
                    422,
                    "meeting_minutes_citation_invalid",
                    "纪要引用缺少有效的转写定位",
                )
            normalized.append(
                {
                    "locator": locator,
                    "locatorKind": locator_kind,
                    "pageNo": (
                        int(item["pageNo"]) if item.get("pageNo") is not None else None
                    ),
                    "paragraphNo": (
                        int(item["paragraphNo"])
                        if item.get("paragraphNo") is not None
                        else None
                    ),
                    "locatorHash": sha256_text(locator),
                }
            )
        return normalized

    def create_minutes_draft(
        self,
        *,
        client_id: str,
        meeting_id: str,
        recording_id: str,
        title: str | None = None,
        minutes_markdown: str | None = None,
        citations: Sequence[Mapping[str, Any]] = (),
        force: bool = False,
    ) -> dict[str, Any]:
        context = self._context()
        with runtime_connection(self.database_path, "local") as connection:
            self._require_meeting(
                connection,
                context,
                client_id=client_id,
                meeting_id=meeting_id,
            )
            recording = connection.execute(
                "SELECT r.*,m.title AS meeting_title,tv.id AS transcription_id,"
                "tv.version AS transcription_version,tv.object_manifest_id AS "
                "transcript_manifest_id,tv.integrity_hash AS transcript_hash "
                "FROM recordings r JOIN meetings m ON m.scope_id=r.scope_id "
                "AND m.id=r.meeting_id LEFT JOIN transcription_versions tv "
                "ON tv.scope_id=r.scope_id AND tv.id=r.current_transcription_version_id "
                "WHERE r.scope_id=? AND r.id=? AND r.meeting_id=?",
                (context.scope_id, recording_id, meeting_id),
            ).fetchone()
            if recording is None:
                raise GC08DomainError(404, "recording_missing", "当前会议没有该录音")
            current = self._latest_attempt(
                connection,
                context,
                recording_id=recording_id,
                processor_kind=self.MINUTES_PROCESSOR,
            )
            if current is not None and not force and str(current["status"]) == "ready":
                return self.recording_detail(
                    client_id=client_id,
                    meeting_id=meeting_id,
                    recording_id=recording_id,
                )
            attempt_id, _ = self._start_attempt(
                connection,
                context,
                recording_id=recording_id,
                processor_kind=self.MINUTES_PROCESSOR,
            )
            transcript_manifest = connection.execute(
                "SELECT * FROM object_manifests WHERE scope_id=? AND id=?",
                (context.scope_id, recording["transcript_manifest_id"]),
            ).fetchone()
        if recording["transcription_id"] is None or transcript_manifest is None:
            error: GC08DomainError = GC08BlockedError(
                424,
                "meeting_transcription_not_ready",
                "请先完成非空转写，再生成纪要",
            )
            transcript = ""
        else:
            transcript_path = self._managed_path(
                Path(str(transcript_manifest["local_original_path"] or ""))
            )
            try:
                transcript = transcript_path.read_text(encoding="utf-8").strip()
            except (OSError, UnicodeDecodeError):
                transcript = ""
            if not transcript or sha256_text(transcript) != str(
                recording["transcript_hash"] or ""
            ):
                error = GC08BlockedError(
                    409,
                    "meeting_transcript_integrity_failed",
                    "完整转写不可用或校验失败，已停止生成纪要",
                )
            else:
                error = None  # type: ignore[assignment]

        normalized_title = str(title or recording["meeting_title"] or "会议纪要").strip()
        manual = str(minutes_markdown or "").strip()
        output: dict[str, Any] | None = None
        if error is None:
            if manual:
                output = {
                    "title": normalized_title,
                    "minutesMarkdown": manual,
                    "citations": list(citations),
                    "actionCandidates": [],
                }
            elif self.minutes_runner is None:
                error = GC08BlockedError(
                    424,
                    "meeting_minutes_agent_not_connected",
                    "会议纪要自动整理未接通；可手工编辑纪要后发布",
                )
            else:
                try:
                    output = parse_minutes_model_output(
                        self.minutes_runner(transcript, normalized_title)
                    )
                except GC08DomainError as exc:
                    error = exc
                except Exception as exc:  # noqa: BLE001
                    error = GC08RetryableError(
                        503,
                        "meeting_minutes_generation_failed",
                        f"纪要生成暂时失败：{exc.__class__.__name__}",
                    )
        if error is not None:
            retryable = isinstance(error, GC08RetryableError)
            with runtime_connection(self.database_path, "local") as connection:
                self._finish_attempt(
                    connection,
                    attempt_id=attempt_id,
                    status="failed_retryable" if retryable else "blocked",
                    error_code=error.code,
                    message=error.message,
                    retryable=retryable,
                )
                connection.commit()
            return self.recording_detail(
                client_id=client_id,
                meeting_id=meeting_id,
                recording_id=recording_id,
            )

        assert output is not None
        body = str(output["minutesMarkdown"]).strip()
        if not body:
            raise AssertionError("empty minutes cannot reach persistence")
        normalized_citations = self._normalize_citations(
            list(citations) or list(output.get("citations") or []),
            transcript_characters=len(transcript),
        )
        document_id = _stable_id("meeting_minutes", context.scope_id, recording_id)
        now = utc_now()
        with runtime_connection(self.database_path, "local") as connection:
            current_document = connection.execute(
                "SELECT * FROM knowledge_documents WHERE scope_id=? AND id=?",
                (context.scope_id, document_id),
            ).fetchone()
            version = (
                int(current_document["current_version"] or 0) + 1
                if current_document is not None
                else 1
            )
            document_version_id = _stable_id(
                "meeting_minutes_version",
                context.scope_id,
                document_id,
                version,
            )
            manifest_id = _stable_id("manifest_minutes", document_version_id)
            target = self.recordings_root / "minutes" / (
                f"{sha256_text(recording_id)[:24]}-v{version}.md"
            )
            _atomic_write(target, body.encode("utf-8"))
            content_hash = sha256_text(body)
            evidence_ids = [
                _stable_id(
                    "meeting_evidence",
                    context.scope_id,
                    document_version_id,
                    item["locatorKind"],
                    item["locator"],
                )
                for item in normalized_citations
            ]
            receipt = canonical_json(
                {
                    "boundary": "member_device_minutes_draft",
                    "evidenceLinkIds": evidence_ids,
                    "actionCandidates": list(output.get("actionCandidates") or []),
                    "localPathUploaded": False,
                    "transcriptUploaded": False,
                }
            )
            try:
                connection.execute("BEGIN IMMEDIATE")
                connection.execute(
                    "INSERT INTO object_manifests (id,scope_id,storage_key,"
                    "content_hash,lifecycle_state,receipt,holder_role,"
                    "holder_instance_id,storage_kind,byte_size,media_type,"
                    "availability_state,receipt_hash,created_at,verified_at,"
                    "deleted_at,authority_role,origin_instance_id,"
                    "local_original_path) VALUES (?,?,?,?,'active',?,"
                    "'member_device',?,'local_minutes_draft',?,'text/markdown',"
                    "'ready',?,?,?,NULL,'local',?,?)",
                    (
                        manifest_id,
                        context.scope_id,
                        target.relative_to(self.recordings_root).as_posix(),
                        content_hash,
                        receipt,
                        context.sandbox_id,
                        len(body.encode("utf-8")),
                        sha256_text(receipt),
                        now,
                        now,
                        context.origin_instance_id,
                        str(target),
                    ),
                )
                connection.execute(
                    "INSERT INTO secured_resources (id,scope_id,resource_kind,"
                    "lifecycle_state,version,resource_type_key,created_at,updated_at,"
                    "deleted_at,authority_role,origin_instance_id) VALUES (?,?,"
                    "'knowledge_document','active',1,'meeting_minutes',?,?,NULL,"
                    "'local',?) ON CONFLICT(id) DO UPDATE SET updated_at=excluded.updated_at",
                    (
                        document_id,
                        context.scope_id,
                        now,
                        now,
                        context.origin_instance_id,
                    ),
                )
                connection.execute(
                    "INSERT INTO knowledge_documents (id,scope_id,source_asset_id,"
                    "client_id,current_version,owner_membership_id,title,document_kind,"
                    "visibility_scope,parse_state,publication_state,published_at,"
                    "version,lifecycle_state,created_at,updated_at,deleted_at,"
                    "sandbox_id,source_version,projection_state,projected_at,stale_at,"
                    "lease_expires_at) VALUES (?,?,NULL,?,?,?,?,'meeting_minutes',"
                    "'member_device','ready','draft',NULL,1,'active',?,?,NULL,?,1,"
                    "'current',?,NULL,NULL) ON CONFLICT(id) DO UPDATE SET "
                    "current_version=excluded.current_version,title=excluded.title,"
                    "parse_state='ready',publication_state='draft',published_at=NULL,"
                    "version=knowledge_documents.version+1,lifecycle_state='active',"
                    "updated_at=excluded.updated_at,deleted_at=NULL,sandbox_id=excluded.sandbox_id,"
                    "projection_state='current',projected_at=excluded.projected_at",
                    (
                        document_id,
                        context.scope_id,
                        client_id,
                        version,
                        context.membership_id,
                        str(output.get("title") or normalized_title).strip(),
                        now,
                        now,
                        context.sandbox_id,
                        now,
                    ),
                )
                connection.execute(
                    "INSERT INTO document_versions (id,scope_id,document_id,version,"
                    "content_hash,created_at,object_manifest_id,source_asset_version,"
                    "publication_state,created_by_membership_id,origin_instance_id,"
                    "integrity_hash,sandbox_id,source_version,projection_state,"
                    "projected_at,stale_at,lease_expires_at) VALUES (?,?,?,?,?,?,?,NULL,"
                    "'draft',?,?,?, ?,1,'current',?,NULL,NULL)",
                    (
                        document_version_id,
                        context.scope_id,
                        document_id,
                        version,
                        content_hash,
                        now,
                        manifest_id,
                        context.membership_id,
                        context.origin_instance_id,
                        content_hash,
                        context.sandbox_id,
                        now,
                    ),
                )
                for evidence_id, item in zip(
                    evidence_ids,
                    normalized_citations,
                    strict=True,
                ):
                    connection.execute(
                        "INSERT INTO evidence_links (id,scope_id,fact_id,"
                        "source_object_id,source_version,locator,source_object_kind,"
                        "locator_kind,page_no,paragraph_no,locator_hash,created_at) "
                        "VALUES (?,?,NULL,?,?,?,?,?,?,?,?,?)",
                        (
                            evidence_id,
                            context.scope_id,
                            recording["transcription_id"],
                            int(recording["transcription_version"] or 1),
                            item["locator"],
                            "transcription_version",
                            item["locatorKind"],
                            item["pageNo"],
                            item["paragraphNo"],
                            item["locatorHash"],
                            now,
                        ),
                    )
                self._finish_attempt(
                    connection,
                    attempt_id=attempt_id,
                    status="ready",
                )
                connection.commit()
            except Exception:
                connection.rollback()
                target.unlink(missing_ok=True)
                raise
        return self.recording_detail(
            client_id=client_id,
            meeting_id=meeting_id,
            recording_id=recording_id,
        )

    @staticmethod
    def downstream_adapters(
        *,
        client_id: str,
        meeting_id: str,
        minutes_document_id: str,
        minutes_version: int,
    ) -> dict[str, Any]:
        basis = {
            "clientId": client_id,
            "meetingId": meeting_id,
            "sourceDocumentId": minutes_document_id,
            "sourceVersion": minutes_version,
        }
        return {
            "taskCommand": {
                "interface": "gc04.task-command.v1",
                "state": "waiting_for_formal_command",
                "payloadBasis": basis,
            },
            "eventLineReferenceCommand": {
                "interface": "gc03.event-line-reference-command.v1",
                "state": "waiting_for_formal_command",
                "payloadBasis": basis,
            },
        }

    def _minutes_payload(
        self,
        connection: Any,
        context: GC08LocalContext,
        *,
        document_id: str,
        include_body: bool,
    ) -> dict[str, Any] | None:
        row = connection.execute(
            "SELECT d.*,v.id AS document_version_id,v.content_hash,"
            "v.publication_state AS version_publication_state,m.receipt,"
            "m.local_original_path FROM knowledge_documents d "
            "JOIN document_versions v ON v.scope_id=d.scope_id "
            "AND v.document_id=d.id AND v.version=d.current_version "
            "JOIN object_manifests m ON m.scope_id=v.scope_id "
            "AND m.id=v.object_manifest_id WHERE d.scope_id=? AND d.id=?",
            (context.scope_id, document_id),
        ).fetchone()
        if row is None:
            return None
        body = ""
        if include_body:
            path = self._managed_path(Path(str(row["local_original_path"] or "")))
            body = path.read_text(encoding="utf-8").strip()
            if not body or sha256_text(body) != str(row["content_hash"] or ""):
                raise GC08DomainError(
                    409,
                    "meeting_minutes_integrity_failed",
                    "本机纪要草稿校验失败，已停止发布",
                )
        return {
            "documentId": document_id,
            "documentVersionId": str(row["document_version_id"]),
            "version": int(row["current_version"] or 1),
            "title": str(row["title"] or "会议纪要"),
            "publicationState": str(row["publication_state"] or "draft"),
            "contentHash": str(row["content_hash"] or ""),
            "minutesMarkdown": body if include_body else None,
            "receipt": json.loads(str(row["receipt"] or "{}")),
        }

    def cloud_publication_payload(
        self,
        *,
        client_id: str,
        meeting_id: str,
        recording_id: str,
        expected_version: int = 0,
    ) -> dict[str, Any]:
        context = self._context()
        document_id = _stable_id("meeting_minutes", context.scope_id, recording_id)
        with runtime_connection(self.database_path, "local", read_only=True) as connection:
            self._require_meeting(
                connection,
                context,
                client_id=client_id,
                meeting_id=meeting_id,
            )
            recording = connection.execute(
                "SELECT r.*,tv.id AS transcription_id,tv.version AS transcription_version,"
                "tv.status AS transcription_status,tv.language,tv.integrity_hash "
                "FROM recordings r JOIN transcription_versions tv "
                "ON tv.scope_id=r.scope_id AND tv.id=r.current_transcription_version_id "
                "WHERE r.scope_id=? AND r.id=? AND r.meeting_id=?",
                (context.scope_id, recording_id, meeting_id),
            ).fetchone()
            if recording is None:
                raise GC08DomainError(
                    424,
                    "meeting_transcription_not_ready",
                    "没有可发布纪要对应的转写版本",
                )
            minutes = self._minutes_payload(
                connection,
                context,
                document_id=document_id,
                include_body=True,
            )
            if minutes is None:
                raise GC08DomainError(424, "meeting_minutes_missing", "请先生成纪要草稿")
            receipt = _mapping(minutes.get("receipt"))
            evidence_ids = [str(value) for value in receipt.get("evidenceLinkIds") or []]
            evidence: list[dict[str, Any]] = []
            for evidence_id in evidence_ids:
                row = connection.execute(
                    "SELECT * FROM evidence_links WHERE scope_id=? AND id=? "
                    "AND source_object_id=?",
                    (context.scope_id, evidence_id, recording["transcription_id"]),
                ).fetchone()
                if row is None:
                    raise GC08DomainError(
                        409,
                        "meeting_minutes_evidence_missing",
                        "纪要引用链不完整，已停止发布",
                    )
                evidence.append(
                    {
                        "locator": str(row["locator"] or ""),
                        "locatorKind": str(row["locator_kind"] or ""),
                        "pageNo": row["page_no"],
                        "paragraphNo": row["paragraph_no"],
                        "locatorHash": str(row["locator_hash"] or ""),
                    }
                )
        return {
            "recording": {
                "recordingId": recording_id,
                "recordingState": str(recording["recording_state"] or "transcribed"),
                "durationMs": int(recording["duration_ms"] or 0),
                "capturedAt": str(recording["captured_at"] or ""),
                "sourceVersion": int(recording["version"] or 1),
                "deviceIdHash": sha256_text(context.sandbox_id),
            },
            "transcription": {
                "transcriptionId": str(recording["transcription_id"]),
                "version": int(recording["transcription_version"] or 1),
                "status": str(recording["transcription_status"] or "ready"),
                "language": str(recording["language"] or "auto"),
                "integrityHash": str(recording["integrity_hash"] or ""),
            },
            "minutes": {
                "documentId": document_id,
                "title": minutes["title"],
                "minutesMarkdown": minutes["minutesMarkdown"],
                "contentHash": minutes["contentHash"],
                "expectedVersion": max(0, int(expected_version)),
                "evidence": evidence,
                "actionCandidates": list(receipt.get("actionCandidates") or []),
            },
        }

    def record_cloud_publication(
        self,
        *,
        client_id: str,
        meeting_id: str,
        recording_id: str,
        cloud_version: int,
        cloud_instance_id: str,
    ) -> dict[str, Any]:
        context = self._context()
        document_id = _stable_id("meeting_minutes", context.scope_id, recording_id)
        now = utc_now()
        with runtime_connection(self.database_path, "local") as connection:
            self._require_meeting(
                connection,
                context,
                client_id=client_id,
                meeting_id=meeting_id,
            )
            published_version_id = _stable_id(
                "meeting_minutes_published_projection",
                context.scope_id,
                document_id,
                cloud_version,
            )
            if connection.execute(
                "SELECT 1 FROM document_versions WHERE scope_id=? AND id=?",
                (context.scope_id, published_version_id),
            ).fetchone() is not None:
                return self.recording_detail(
                    client_id=client_id,
                    meeting_id=meeting_id,
                    recording_id=recording_id,
                )
            current = connection.execute(
                "SELECT d.*,v.object_manifest_id,v.content_hash FROM "
                "knowledge_documents d JOIN document_versions v "
                "ON v.scope_id=d.scope_id AND v.document_id=d.id "
                "AND v.version=d.current_version WHERE d.scope_id=? AND d.id=?",
                (context.scope_id, document_id),
            ).fetchone()
            if current is None:
                raise GC08DomainError(404, "meeting_minutes_missing", "本机纪要草稿不存在")
            local_version = int(current["current_version"] or 1) + 1
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                "INSERT INTO document_versions (id,scope_id,document_id,version,"
                "content_hash,created_at,object_manifest_id,source_asset_version,"
                "publication_state,created_by_membership_id,origin_instance_id,"
                "integrity_hash,sandbox_id,source_version,projection_state,projected_at,"
                "stale_at,lease_expires_at) VALUES (?,?,?,?,?,?,?,NULL,'published',"
                "?,?,?, ?,?,'current',?,NULL,NULL)",
                (
                    published_version_id,
                    context.scope_id,
                    document_id,
                    local_version,
                    current["content_hash"],
                    now,
                    current["object_manifest_id"],
                    context.membership_id,
                    cloud_instance_id,
                    current["content_hash"],
                    context.sandbox_id,
                    max(1, int(cloud_version)),
                    now,
                ),
            )
            connection.execute(
                "UPDATE knowledge_documents SET current_version=?,"
                "publication_state='published',published_at=?,version=version+1,"
                "updated_at=?,source_version=?,projection_state='current',"
                "projected_at=?,stale_at=NULL WHERE scope_id=? AND id=?",
                (
                    local_version,
                    now,
                    now,
                    max(1, int(cloud_version)),
                    now,
                    context.scope_id,
                    document_id,
                ),
            )
            connection.execute(
                "UPDATE recordings SET recording_state='minutes_published',"
                "version=version+1,updated_at=? WHERE scope_id=? AND id=?",
                (now, context.scope_id, recording_id),
            )
            connection.commit()
        return self.recording_detail(
            client_id=client_id,
            meeting_id=meeting_id,
            recording_id=recording_id,
        )

    def recording_detail(
        self,
        *,
        client_id: str,
        meeting_id: str,
        recording_id: str,
    ) -> dict[str, Any]:
        context = self._context()
        document_id = _stable_id("meeting_minutes", context.scope_id, recording_id)
        with runtime_connection(self.database_path, "local", read_only=True) as connection:
            self._require_meeting(
                connection,
                context,
                client_id=client_id,
                meeting_id=meeting_id,
            )
            recording = connection.execute(
                "SELECT r.*,tv.id AS transcription_id,tv.version AS transcription_version,"
                "tv.status AS transcription_status,tv.language,tv.integrity_hash,"
                "recording_manifest.id AS recording_manifest_id,"
                "recording_manifest.content_hash AS recording_content_hash,"
                "recording_manifest.byte_size AS recording_byte_size,"
                "recording_manifest.availability_state AS recording_availability_state,"
                "recording_manifest.local_original_path AS recording_local_path,"
                "transcript_manifest.id AS transcription_manifest_id,"
                "transcript_manifest.content_hash AS transcription_content_hash,"
                "transcript_manifest.byte_size AS transcription_byte_size,"
                "transcript_manifest.availability_state AS transcription_availability_state,"
                "transcript_manifest.local_original_path AS transcription_local_path "
                "FROM recordings r JOIN object_manifests recording_manifest "
                "ON recording_manifest.scope_id=r.scope_id "
                "AND recording_manifest.id=r.object_manifest_id "
                "LEFT JOIN transcription_versions tv "
                "ON tv.scope_id=r.scope_id AND tv.id=r.current_transcription_version_id "
                "LEFT JOIN object_manifests transcript_manifest "
                "ON transcript_manifest.scope_id=tv.scope_id "
                "AND transcript_manifest.id=tv.object_manifest_id "
                "WHERE r.scope_id=? AND r.id=? AND r.meeting_id=?",
                (context.scope_id, recording_id, meeting_id),
            ).fetchone()
            if recording is None:
                raise GC08DomainError(404, "recording_missing", "当前会议没有该录音")
            attempts = {}
            for processor in (self.TRANSCRIPT_PROCESSOR, self.MINUTES_PROCESSOR):
                attempt = self._latest_attempt(
                    connection,
                    context,
                    recording_id=recording_id,
                    processor_kind=processor,
                )
                attempts[processor] = dict(attempt) if attempt is not None else None
            minutes = self._minutes_payload(
                connection,
                context,
                document_id=document_id,
                include_body=True,
            )
        recording_path = self._reconcile_manifest_path(
            context=context,
            manifest_id=recording["recording_manifest_id"],
            content_hash=recording["recording_content_hash"],
            byte_size=recording["recording_byte_size"],
            current_path=recording["recording_local_path"],
            availability_state=recording["recording_availability_state"],
            search_root=self.recordings_root,
        )
        transcription_path = self._reconcile_manifest_path(
            context=context,
            manifest_id=recording["transcription_manifest_id"],
            content_hash=recording["transcription_content_hash"],
            byte_size=recording["transcription_byte_size"],
            current_path=recording["transcription_local_path"],
            availability_state=recording["transcription_availability_state"],
            search_root=self.recordings_root / "transcripts",
        )
        transcription_attempt = attempts[self.TRANSCRIPT_PROCESSOR]
        minutes_attempt = attempts[self.MINUTES_PROCESSOR]
        result = {
            "clientId": client_id,
            "meetingId": meeting_id,
            "recordingId": recording_id,
            "recordingState": str(recording["recording_state"] or "captured"),
            "durationMs": int(recording["duration_ms"] or 0),
            "capturedAt": recording["captured_at"],
            "localFiles": {
                "recordingPath": recording_path,
                "transcriptionPath": transcription_path,
            },
            "transcription": {
                "transcriptionId": recording["transcription_id"],
                "version": recording["transcription_version"],
                "status": (
                    str(recording["transcription_status"])
                    if recording["transcription_id"] is not None
                    else str((transcription_attempt or {}).get("status") or "not_requested")
                ),
                "language": recording["language"],
                "integrityHash": recording["integrity_hash"],
                "errorCode": (transcription_attempt or {}).get("error_code"),
                "message": (transcription_attempt or {}).get("error_message_safe"),
                "retryable": str((transcription_attempt or {}).get("status") or "")
                == "failed_retryable",
            },
            "minutes": minutes,
            "minutesProcessing": {
                "status": str((minutes_attempt or {}).get("status") or "not_requested"),
                "errorCode": (minutes_attempt or {}).get("error_code"),
                "message": (minutes_attempt or {}).get("error_message_safe"),
                "retryable": str((minutes_attempt or {}).get("status") or "")
                == "failed_retryable",
            },
        }
        if minutes is not None:
            result["downstreamAdapters"] = self.downstream_adapters(
                client_id=client_id,
                meeting_id=meeting_id,
                minutes_document_id=document_id,
                minutes_version=int(minutes["version"]),
            )
        return result

    def latest_recording_detail(
        self,
        *,
        client_id: str,
        meeting_id: str,
    ) -> dict[str, Any] | None:
        context = self._context()
        with runtime_connection(self.database_path, "local", read_only=True) as connection:
            self._require_meeting(
                connection,
                context,
                client_id=client_id,
                meeting_id=meeting_id,
            )
            row = connection.execute(
                "SELECT id FROM recordings WHERE scope_id=? AND meeting_id=? "
                "AND lifecycle_state='active' ORDER BY captured_at DESC,updated_at DESC,id DESC LIMIT 1",
                (context.scope_id, meeting_id),
            ).fetchone()
        if row is None:
            return None
        return self.recording_detail(
            client_id=client_id,
            meeting_id=meeting_id,
            recording_id=str(row["id"]),
        )

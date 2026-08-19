"""Device-local extraction for supported public project links.

The extractor deliberately has no database or cloud access.  Public HTML,
temporary media and ASR results stay on the current device; the caller decides
which current-sandbox ``storage_object`` receives the extracted text.
"""

from __future__ import annotations

import ipaddress
import json
import html
import re
import socket
import tempfile
from dataclasses import dataclass
from html import unescape
from html.parser import HTMLParser
from pathlib import Path
from typing import Any, Callable
from urllib.parse import urljoin, urlparse

import httpx

from .local_asr.engine import transcribe_recording
from .runtime import LocalRuntimeError


_SUPPORTED_HOSTS = {
    "bilibili": ("bilibili.com", "b23.tv"),
    "xiaohongshu": ("xiaohongshu.com", "xhslink.com", "xhslink.cn", "xhs.cn"),
    "wechat_article": ("mp.weixin.qq.com",),
}
_SHORT_LINK_HOSTS = {"b23.tv", "xhslink.com", "xhslink.cn", "xhs.cn"}
_MAX_RESPONSE_BYTES = 10 * 1024 * 1024
_MAX_MEDIA_BYTES = 512 * 1024 * 1024
_MAX_TEXT_CHARS = 2_000_000
_MAX_MEDIA_DURATION_SECONDS = 4 * 60 * 60
_URL_IN_TEXT = re.compile(r"https?://[^\s<>\"'，。；、）】]+", re.IGNORECASE)
_BILIBILI_ID = re.compile(r"^BV[0-9A-Za-z]{8,}$", re.IGNORECASE)
_BILIBILI_VIDEO_PATH = re.compile(
    r"^/video/(?:BV[0-9A-Za-z]{8,}|av[0-9]+)(?:/)?$",
    re.IGNORECASE,
)
_XIAOHONGSHU_NOTE_PATH = re.compile(
    r"^/(?:explore|discovery/item)/[0-9A-Za-z]{8,}(?:/)?$",
    re.IGNORECASE,
)
_SUBTITLE_SUFFIXES = {".vtt", ".srt", ".ass", ".lrc"}
_MEDIA_SKIP_SUFFIXES = _SUBTITLE_SUFFIXES | {
    ".description",
    ".json",
    ".jpg",
    ".jpeg",
    ".png",
    ".webp",
}
_VOID_HTML_TAGS = {
    "area",
    "base",
    "br",
    "col",
    "embed",
    "hr",
    "img",
    "input",
    "link",
    "meta",
    "param",
    "source",
    "track",
    "wbr",
}


class LinkMaterialFetchError(LocalRuntimeError):
    """An extraction error carrying the renderer's strict reliability state."""

    def __init__(
        self,
        status_code: int,
        code: str,
        message: str,
        *,
        state: str,
        retryable: bool,
    ):
        super().__init__(status_code, code, message)
        self.state = state
        self.retryable = retryable


@dataclass(frozen=True)
class _MediaDownload:
    title: str
    description: str
    source_url: str
    media_path: Path | None
    subtitle_paths: tuple[Path, ...]
    duration_seconds: float | None


def _blocked(code: str, message: str, status_code: int = 422) -> LinkMaterialFetchError:
    return LinkMaterialFetchError(
        status_code,
        code,
        message,
        state="blocked",
        retryable=False,
    )


def _retryable(code: str, message: str) -> LinkMaterialFetchError:
    return LinkMaterialFetchError(
        503,
        code,
        message,
        state="failed_retryable",
        retryable=True,
    )


def _not_connected(code: str, message: str) -> LinkMaterialFetchError:
    return LinkMaterialFetchError(
        501,
        code,
        message,
        state="not_connected",
        retryable=False,
    )


def _platform(hostname: str) -> str:
    host = hostname.lower().rstrip(".")
    for platform, suffixes in _SUPPORTED_HOSTS.items():
        if any(host == suffix or host.endswith(f".{suffix}") for suffix in suffixes):
            return platform
    return ""


def _extract_supported_url(value: str) -> str:
    normalized = value.strip()
    if _BILIBILI_ID.fullmatch(normalized):
        return f"https://www.bilibili.com/video/{normalized}"
    match = _URL_IN_TEXT.search(normalized)
    return (
        match.group(0).rstrip(".,;:!?)]}")
        if match
        else normalized
    )


def _validated_url(value: str) -> tuple[str, str]:
    normalized = _extract_supported_url(value)
    try:
        parsed = urlparse(normalized)
        hostname = parsed.hostname
        port = parsed.port
    except ValueError as exc:
        raise _blocked(
            "link_import_url_invalid",
            "资料链接格式无效",
        ) from exc
    if parsed.scheme == "http" and parsed.hostname and _platform(parsed.hostname):
        normalized = "https://" + normalized.split("://", 1)[1]
        parsed = urlparse(normalized)
    if (
        parsed.scheme != "https"
        or not hostname
        or parsed.username is not None
        or parsed.password is not None
        or port not in {None, 443}
    ):
        raise _blocked(
            "link_import_url_invalid",
            "资料链接必须是无账号信息和自定义端口的 HTTPS 链接",
        )
    platform = _platform(hostname)
    if not platform:
        raise _blocked(
            "link_import_platform_unsupported",
            "当前仅支持哔哩哔哩、小红书和微信公众号资料链接",
        )
    if (
        platform == "wechat_article"
        and parsed.path != "/s"
        and not parsed.path.startswith("/s/")
    ):
        raise _blocked(
            "link_import_wechat_article_required",
            "该微信链接不是可导入的公众号文章正文链接",
        )
    try:
        addresses = {
            item[4][0]
            for item in socket.getaddrinfo(
                parsed.hostname,
                443,
                type=socket.SOCK_STREAM,
            )
        }
    except OSError as exc:
        raise _retryable(
            "link_import_dns_failed",
            "资料链接域名暂时无法解析，可重试",
        ) from exc
    if not addresses:
        raise _retryable(
            "link_import_dns_failed",
            "资料链接域名没有可用地址，可重试",
        )
    for address in addresses:
        ip = ipaddress.ip_address(address)
        if (
            ip.is_private
            or ip.is_loopback
            or ip.is_link_local
            or ip.is_multicast
            or ip.is_reserved
            or ip.is_unspecified
        ):
            raise _blocked(
                "link_import_private_address_forbidden",
                "资料链接不能指向本机或内网地址",
                403,
            )
    return normalized, platform


def _validated_media_content_url(url: str, platform: str) -> str:
    normalized, current_platform = _validated_url(url)
    if current_platform != platform:
        raise _blocked(
            "link_import_redirect_platform_changed",
            "资料链接跳转到了另一个平台",
            403,
        )
    parsed = urlparse(normalized)
    if platform == "bilibili":
        valid_path = bool(_BILIBILI_VIDEO_PATH.fullmatch(parsed.path))
    else:
        valid_path = bool(_XIAOHONGSHU_NOTE_PATH.fullmatch(parsed.path))
    if not valid_path:
        raise _blocked(
            "link_import_content_path_unsupported",
            "该地址不是当前支持的公开视频或公开笔记正文链接",
        )
    return normalized


def _response_bytes(response: httpx.Response) -> bytes:
    chunks: list[bytes] = []
    total = 0
    for chunk in response.iter_bytes(256 * 1024):
        total += len(chunk)
        if total > _MAX_RESPONSE_BYTES:
            raise _blocked(
                "link_import_response_too_large",
                "网页正文超过 10MB，不能作为项目资料导入",
                413,
            )
        chunks.append(chunk)
    return b"".join(chunks)


def _response_error(response: httpx.Response) -> LinkMaterialFetchError:
    if response.status_code in {401, 403, 412}:
        return _blocked(
            "link_import_authentication_required",
            "该平台拒绝匿名读取；严格新版不会读取浏览器 Cookie，请改用公开链接",
            status_code=409,
        )
    if response.status_code == 429 or response.status_code >= 500:
        return _retryable(
            "link_import_remote_http_error",
            f"资料平台返回 HTTP {response.status_code}，可重试",
        )
    return _blocked(
        "link_import_remote_http_error",
        f"资料平台返回 HTTP {response.status_code}，请检查链接是否仍然有效",
    )


def _fetch_public_html(url: str, platform: str) -> tuple[str, str, str]:
    current = url
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Macintosh; Apple Silicon Mac OS X) "
            "AppleWebKit/537.36 Chrome/126 Safari/537.36 "
            "YiyuThinkTankStrict/1"
        ),
        "Accept": "text/html,application/xhtml+xml,text/plain;q=0.9,*/*;q=0.1",
    }
    try:
        with httpx.Client(
            follow_redirects=False,
            timeout=httpx.Timeout(15.0, read=45.0),
            trust_env=False,
            headers=headers,
        ) as client:
            for _ in range(6):
                current, current_platform = _validated_url(current)
                if current_platform != platform:
                    raise _blocked(
                        "link_import_redirect_platform_changed",
                        "资料链接跳转到了另一个平台",
                        403,
                    )
                with client.stream("GET", current) as response:
                    if response.status_code in {301, 302, 303, 307, 308}:
                        location = response.headers.get("location", "")
                        if not location:
                            raise _blocked(
                                "link_import_redirect_invalid",
                                "资料链接返回了无效跳转",
                            )
                        current = urljoin(current, location)
                        continue
                    if response.status_code >= 400:
                        raise _response_error(response)
                    content_type = response.headers.get("content-type", "")
                    raw = _response_bytes(response)
                    encoding = response.encoding or "utf-8"
                    return (
                        current,
                        content_type,
                        raw.decode(encoding, errors="replace"),
                    )
            raise _blocked(
                "link_import_redirect_limit",
                "资料链接跳转次数过多",
            )
    except LinkMaterialFetchError:
        raise
    except httpx.TimeoutException as exc:
        raise _retryable(
            "link_import_fetch_timeout",
            "资料网页读取超时，可重试",
        ) from exc
    except httpx.HTTPError as exc:
        raise _retryable(
            "link_import_fetch_failed",
            "资料网页读取失败，可重试",
        ) from exc


def _resolve_short_url(url: str, platform: str) -> str:
    hostname = (urlparse(url).hostname or "").lower()
    if hostname not in _SHORT_LINK_HOSTS:
        return url
    current = url
    try:
        with httpx.Client(
            follow_redirects=False,
            timeout=httpx.Timeout(15.0, read=15.0),
            trust_env=False,
            headers={"User-Agent": "YiyuThinkTankStrict/1"},
        ) as client:
            for _ in range(6):
                current, current_platform = _validated_url(current)
                if current_platform != platform:
                    raise _blocked(
                        "link_import_redirect_platform_changed",
                        "资料短链接跳转到了另一个平台",
                        403,
                    )
                with client.stream("GET", current) as response:
                    if response.status_code in {301, 302, 303, 307, 308}:
                        location = response.headers.get("location", "")
                        if not location:
                            raise _blocked(
                                "link_import_redirect_invalid",
                                "资料短链接返回了无效跳转",
                            )
                        current = urljoin(current, location)
                        continue
                    if response.status_code >= 400:
                        raise _response_error(response)
                    return current
            raise _blocked(
                "link_import_redirect_limit",
                "资料短链接跳转次数过多",
            )
    except LinkMaterialFetchError:
        raise
    except httpx.TimeoutException as exc:
        raise _retryable(
            "link_import_fetch_timeout",
            "资料短链接解析超时，可重试",
        ) from exc
    except httpx.HTTPError as exc:
        raise _retryable(
            "link_import_fetch_failed",
            "资料短链接解析失败，可重试",
        ) from exc


class _ReadableHtml(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.title = ""
        self.description = ""
        self._in_title = False
        self._ignored_depth = 0
        self._texts: list[str] = []

    def handle_starttag(
        self,
        tag: str,
        attrs: list[tuple[str, str | None]],
    ) -> None:
        lowered = tag.lower()
        if lowered in {"script", "style", "noscript", "svg"}:
            self._ignored_depth += 1
        if lowered == "title":
            self._in_title = True
        if lowered == "meta":
            values = {key.lower(): str(value or "") for key, value in attrs}
            name = (values.get("name") or values.get("property") or "").lower()
            if name in {"description", "og:description"} and not self.description:
                self.description = values.get("content", "").strip()
            if name in {"og:title", "twitter:title"} and not self.title:
                self.title = values.get("content", "").strip()

    def handle_endtag(self, tag: str) -> None:
        lowered = tag.lower()
        if lowered == "title":
            self._in_title = False
        if lowered in {"script", "style", "noscript", "svg"} and self._ignored_depth:
            self._ignored_depth -= 1

    def handle_data(self, data: str) -> None:
        value = re.sub(r"\s+", " ", data).strip()
        if not value or self._ignored_depth:
            return
        if self._in_title and not self.title:
            self.title = value
        self._texts.append(value)

    def readable_text(self) -> str:
        return _deduplicated_lines(self._texts)


class _WechatArticleHtml(HTMLParser):
    """Extract the published article body instead of the surrounding shell."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.title = ""
        self.author = ""
        self._capture_depth = 0
        self._ignored_depth = 0
        self._capture_title_depth = 0
        self._capture_author_depth = 0
        self._lines: list[str] = []

    def handle_starttag(
        self,
        tag: str,
        attrs: list[tuple[str, str | None]],
    ) -> None:
        lowered = tag.lower()
        values = {key.lower(): str(value or "") for key, value in attrs}
        classes = set(values.get("class", "").split())
        if lowered == "meta":
            name = (values.get("name") or values.get("property") or "").lower()
            if name in {"og:title", "twitter:title"} and not self.title:
                self.title = values.get("content", "").strip()
        if (
            values.get("id") == "js_content"
            or "rich_media_content" in classes
        ):
            self._capture_depth = 1
        elif self._capture_depth and lowered not in _VOID_HTML_TAGS:
            self._capture_depth += 1
        if (
            values.get("id") == "activity-name"
            or "rich_media_title" in classes
        ):
            self._capture_title_depth = 1
        elif self._capture_title_depth and lowered not in _VOID_HTML_TAGS:
            self._capture_title_depth += 1
        if values.get("id") == "js_name" or "profile_nickname" in classes:
            self._capture_author_depth = 1
        elif self._capture_author_depth and lowered not in _VOID_HTML_TAGS:
            self._capture_author_depth += 1
        if lowered in {"script", "style", "noscript", "svg"}:
            self._ignored_depth += 1
        if self._capture_depth and lowered in {
            "br",
            "p",
            "section",
            "div",
            "h1",
            "h2",
            "h3",
            "li",
            "blockquote",
        }:
            self._lines.append("\n")

    def handle_endtag(self, tag: str) -> None:
        lowered = tag.lower()
        if self._capture_depth and lowered in {
            "p",
            "section",
            "div",
            "h1",
            "h2",
            "h3",
            "li",
            "blockquote",
        }:
            self._lines.append("\n")
        if lowered in {"script", "style", "noscript", "svg"} and self._ignored_depth:
            self._ignored_depth -= 1
        if self._capture_depth:
            self._capture_depth -= 1
        if self._capture_title_depth:
            self._capture_title_depth -= 1
        if self._capture_author_depth:
            self._capture_author_depth -= 1

    def handle_data(self, data: str) -> None:
        if self._ignored_depth:
            return
        value = re.sub(r"\s+", " ", data).strip()
        if not value:
            return
        if self._capture_title_depth and not self.title:
            self.title = value
        if self._capture_author_depth and not self.author:
            self.author = value
        if self._capture_depth:
            self._lines.append(value)

    def article_text(self) -> str:
        joined = " ".join(self._lines)
        return _deduplicated_lines(re.split(r"\s*\n\s*", joined))


def _deduplicated_lines(values: Any) -> str:
    output: list[str] = []
    previous = ""
    for item in values:
        value = re.sub(r"\s+", " ", str(item)).strip()
        if not value or value == previous:
            continue
        output.append(value)
        previous = value
    return "\n".join(output)


def _clean_subtitle(path: Path) -> str:
    try:
        raw = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""
    output: list[str] = []
    previous = ""
    for line in raw.splitlines():
        value = line.strip()
        if (
            not value
            or value.upper() == "WEBVTT"
            or value.isdigit()
            or "-->" in value
            or value.startswith(("NOTE", "STYLE", "REGION", "Dialogue:"))
        ):
            continue
        value = re.sub(r"<[^>]+>", "", value)
        value = unescape(re.sub(r"\{\\[^}]+\}", "", value))
        value = re.sub(r"\s+", " ", value).strip()
        if not value or value == previous:
            continue
        output.append(value)
        previous = value
    return "\n".join(output).strip()


def _load_ytdlp() -> Any:
    try:
        import yt_dlp  # type: ignore[import-not-found,import-untyped]
    except ImportError as exc:
        raise _not_connected(
            "link_import_ytdlp_not_connected",
            "本机链接媒体提取器未安装，无法读取该音视频",
        ) from exc
    return yt_dlp


def _download_public_media(
    url: str,
    destination: Path,
    *,
    progress: Callable[[int | None], None] | None = None,
) -> _MediaDownload:
    yt_dlp = _load_ytdlp()

    def progress_hook(value: dict[str, Any]) -> None:
        downloaded = value.get("downloaded_bytes")
        total = value.get("total_bytes") or value.get("total_bytes_estimate")
        size = int(downloaded or total or 0)
        if size > _MAX_MEDIA_BYTES:
            raise RuntimeError("YIYU_MEDIA_SIZE_LIMIT")
        if progress is not None:
            progress(size or None)

    def match_filter(
        info: dict[str, Any],
        *,
        incomplete: bool = False,
    ) -> str | None:
        duration = info.get("duration")
        if (
            not incomplete
            and isinstance(duration, (int, float))
            and float(duration) > _MAX_MEDIA_DURATION_SECONDS
        ):
            return "YIYU_MEDIA_DURATION_LIMIT"
        return None

    options: dict[str, Any] = {
        "cachedir": False,
        "cookiefile": None,
        "extract_flat": False,
        "format": "bestaudio/best",
        "fragment_retries": 2,
        "max_downloads": 1,
        "max_filesize": _MAX_MEDIA_BYTES,
        "match_filter": match_filter,
        "noplaylist": True,
        "outtmpl": str(destination / "%(id)s.%(ext)s"),
        "overwrites": True,
        "playlistend": 1,
        "progress_hooks": [progress_hook],
        "proxy": "",
        "quiet": True,
        "restrictfilenames": True,
        "retries": 2,
        "socket_timeout": 30,
        "subtitlesformat": "vtt/srt/best",
        "subtitleslangs": ["zh.*", "zh-Hans", "zh-CN", "zh", "en.*", "en"],
        "verbose": False,
        "writeautomaticsub": True,
        "writesubtitles": True,
        "usenetrc": False,
    }
    try:
        with yt_dlp.YoutubeDL(options) as downloader:
            raw_info = downloader.extract_info(url, download=True)
    except LinkMaterialFetchError:
        raise
    except Exception as exc:  # yt-dlp exposes extractor-specific subclasses.
        lowered = str(exc).lower()
        if "yiyu_media_size_limit" in lowered or "larger than max-filesize" in lowered:
            raise _blocked(
                "link_import_media_too_large",
                "链接音视频超过 512MB，不能在本机导入",
                413,
            ) from exc
        if "yiyu_media_duration_limit" in lowered:
            raise _blocked(
                "link_import_media_duration_too_long",
                "链接音视频超过 4 小时，不能在本机导入",
                413,
            ) from exc
        if any(
            marker in lowered
            for marker in (
                "login",
                "log in",
                "sign in",
                "cookie",
                "账号",
                "登录",
                "仅自己可见",
                "permission",
                "private video",
            )
        ):
            raise _blocked(
                "link_import_authentication_required",
                "该内容需要平台登录或权限；严格新版不会读取浏览器 Cookie",
                409,
            ) from exc
        if any(
            marker in lowered
            for marker in ("timeout", "timed out", "temporarily", "connection", "429")
        ):
            raise _retryable(
                "link_import_media_fetch_failed",
                "链接音视频读取失败，可重试",
            ) from exc
        raise _blocked(
            "link_import_media_unavailable",
            "该公开链接暂时没有可提取的正文、字幕或音频",
            409,
        ) from exc

    info = raw_info if isinstance(raw_info, dict) else {}
    entries = info.get("entries")
    if isinstance(entries, list):
        info = next((item for item in entries if isinstance(item, dict)), {})
    duration_value = info.get("duration")
    duration = (
        float(duration_value)
        if isinstance(duration_value, (int, float))
        else None
    )
    if duration is not None and duration > _MAX_MEDIA_DURATION_SECONDS:
        raise _blocked(
            "link_import_media_duration_too_long",
            "链接音视频超过 4 小时，不能在本机导入",
            413,
        )
    files = tuple(path for path in destination.iterdir() if path.is_file())
    for path in files:
        if path.stat().st_size > _MAX_MEDIA_BYTES:
            raise _blocked(
                "link_import_media_too_large",
                "链接音视频超过 512MB，不能在本机导入",
                413,
            )
    subtitle_paths = tuple(
        path for path in files if path.suffix.lower() in _SUBTITLE_SUFFIXES
    )
    media_path = next(
        (
            path
            for path in files
            if path.suffix.lower() not in _MEDIA_SKIP_SUFFIXES
        ),
        None,
    )
    source_url = str(info.get("webpage_url") or info.get("original_url") or url)
    try:
        source_url, _ = _validated_url(source_url)
    except LinkMaterialFetchError:
        source_url = url
    return _MediaDownload(
        title=str(info.get("title") or "").strip()[:200],
        description=str(info.get("description") or "").strip(),
        source_url=source_url,
        media_path=media_path,
        subtitle_paths=subtitle_paths,
        duration_seconds=duration,
    )


def _media_text(download: _MediaDownload, *, data_root: Path) -> tuple[str, str]:
    subtitle = ""
    for path in sorted(download.subtitle_paths):
        candidate = _clean_subtitle(path)
        if len(candidate) > len(subtitle):
            subtitle = candidate
    if subtitle:
        body = subtitle
        transcript_source = "platform_subtitle"
    elif download.media_path is not None:
        try:
            outcome = transcribe_recording(
                data_root / "models",
                str(download.media_path),
            )
        except FileNotFoundError as exc:
            raise _not_connected(
                "link_import_ffmpeg_not_connected",
                "本机音视频转码器不可用，无法执行本地转写",
            ) from exc
        except RuntimeError as exc:
            message = str(exc)
            if "ASR 模型未就绪" in message:
                raise _blocked(
                    "link_import_asr_model_missing",
                    "本机 ASR 模型未就绪，请先在系统设置中下载",
                    409,
                ) from exc
            if "ffmpeg" in message.lower():
                raise _not_connected(
                    "link_import_ffmpeg_not_connected",
                    "本机音视频转码器不可用，无法执行本地转写",
                ) from exc
            raise _retryable(
                "link_import_asr_failed",
                "本机音视频转写失败，可重试",
            ) from exc
        except Exception as exc:
            raise _retryable(
                "link_import_asr_failed",
                "本机音视频转写失败，可重试",
            ) from exc
        body = str(outcome.dialogue_text or outcome.result.text or "").strip()
        transcript_source = "local_asr"
    else:
        body = ""
        transcript_source = "none"
    description = re.sub(r"\s+", " ", download.description).strip()
    if description and description not in body[:4000]:
        body = f"{description}\n\n{body}".strip()
    if not body:
        raise _blocked(
            "link_import_text_empty",
            "公开链接已读取，但没有可导入的正文、字幕或音频",
            409,
        )
    return body, transcript_source


def _wechat_material(url: str) -> dict[str, Any]:
    final_url, content_type, text = _fetch_public_html(url, "wechat_article")
    if "html" not in content_type.lower() and "<html" not in text[:1000].lower():
        raise _blocked(
            "link_import_content_type_unsupported",
            "该公众号链接没有返回可读取的文章正文",
            415,
        )
    parser = _WechatArticleHtml()
    parser.feed(text)
    readable = parser.article_text().strip()
    if not readable:
        lowered = text.lower()
        if any(
            marker in lowered
            for marker in ("环境异常", "完成验证", "访问过于频繁", "captcha")
        ):
            raise _blocked(
                "link_import_authentication_required",
                "公众号页面要求验证或登录，当前无法匿名提取正文",
                409,
            )
        raise _blocked(
            "link_import_text_empty",
            "公众号页面已打开，但没有提取到文章正文",
            409,
        )
    title = parser.title.strip()[:200] or "微信公众号文章"
    if parser.author:
        readable = f"作者：{parser.author}\n\n{readable}"
    return {
        "platform": "wechat_article",
        "sourceUrl": final_url,
        "title": title,
        "text": readable[:_MAX_TEXT_CHARS].strip(),
        "metadata": {
            "extractionMode": "public_article_body",
            "transcriptSource": "article_html",
            "mediaCacheStatus": "not_downloaded",
            "temporaryFilesCleaned": True,
        },
    }


def _xiaohongshu_text_material(url: str) -> dict[str, Any] | None:
    """Read a public image/text note before attempting media download.

    Xiaohongshu's public page embeds the note title and description in its
    server-rendered state even when yt-dlp correctly reports that the note has
    no video formats.  Treating such a note as missing media used to reject
    perfectly readable text posts.
    """

    final_url, content_type, text = _fetch_public_html(url, "xiaohongshu")
    if "html" not in content_type.lower() and "<html" not in text[:1000].lower():
        return None
    state_start = text.find('"noteDetailMap"')
    state = text[state_start : state_start + 500_000] if state_start >= 0 else text
    matched = re.search(
        r'"title":"((?:\\.|[^"\\])*)","desc":"((?:\\.|[^"\\])*)"',
        state,
    )
    if matched is not None:
        try:
            title = str(json.loads(f'"{matched.group(1)}"')).strip()
            description = str(json.loads(f'"{matched.group(2)}"')).strip()
        except (json.JSONDecodeError, TypeError, ValueError):
            title = ""
            description = ""
    else:
        # Some public-page variants omit the hydrated note map but retain
        # standards-based OpenGraph metadata.  It is still public note text,
        # not a browser-session or cookie fallback.
        title_match = re.search(
            r'<meta\s+property="og:title"\s+content="([^"]+)"',
            text,
            flags=re.IGNORECASE,
        )
        description_match = re.search(
            r'<meta\s+property="og:description"\s+content="([^"]+)"',
            text,
            flags=re.IGNORECASE,
        )
        title = html.unescape(title_match.group(1)).strip() if title_match else ""
        description = (
            html.unescape(description_match.group(1)).strip()
            if description_match
            else ""
        )
    if not description:
        return None
    readable = f"{title}\n\n{description}".strip() if title else description
    return {
        "platform": "xiaohongshu",
        "sourceUrl": final_url,
        "title": (title or "小红书公开笔记")[:200],
        "text": readable[:_MAX_TEXT_CHARS],
        "metadata": {
            "extractionMode": "public_note_text",
            "transcriptSource": "note_description",
            "mediaCacheStatus": "not_downloaded",
            "temporaryFilesCleaned": True,
        },
    }


def fetch_link_material(
    url: str,
    *,
    data_root: Path | None = None,
    progress: Callable[[int | None], None] | None = None,
) -> dict[str, Any]:
    """Extract one supported public link without cookies or cloud raw-body writes."""

    normalized, platform = _validated_url(url)
    if platform == "wechat_article":
        return _wechat_material(normalized)
    normalized = _resolve_short_url(normalized, platform)
    normalized = _validated_media_content_url(normalized, platform)
    if platform == "xiaohongshu":
        try:
            public_note = _xiaohongshu_text_material(normalized)
        except LinkMaterialFetchError:
            # Text-first extraction is an enhancement.  A transient HTML
            # failure must not block a public video note that yt-dlp can read.
            public_note = None
        if public_note is not None:
            return public_note
    root = Path(data_root).resolve() if data_root is not None else Path(tempfile.gettempdir())
    temporary_root = root / "tmp" / "link-material"
    temporary_root.mkdir(parents=True, exist_ok=True)
    try:
        with tempfile.TemporaryDirectory(
            prefix="extract-",
            dir=temporary_root,
        ) as directory:
            download = _download_public_media(
                normalized,
                Path(directory),
                progress=progress,
            )
            readable, transcript_source = _media_text(
                download,
                data_root=root,
            )
            title = download.title or (
                "哔哩哔哩链接资料"
                if platform == "bilibili"
                else "小红书链接资料"
            )
            return {
                "platform": platform,
                "sourceUrl": download.source_url,
                "title": title[:200],
                "text": readable[:_MAX_TEXT_CHARS].strip(),
                "metadata": {
                    "extractionMode": "anonymous_public_media",
                    "transcriptSource": transcript_source,
                    "mediaCacheStatus": "cleaned",
                    "temporaryFilesCleaned": True,
                    "durationSeconds": download.duration_seconds,
                },
            }
    except LinkMaterialFetchError:
        raise
    except OSError as exc:
        raise _retryable(
            "link_import_temporary_storage_failed",
            "本机临时目录不可用，链接资料导入可重试",
        ) from exc

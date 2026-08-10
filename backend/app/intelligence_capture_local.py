from __future__ import annotations

import html
import ipaddress
import json
import re
import socket
import xml.etree.ElementTree as ET
from dataclasses import replace
from dataclasses import dataclass
from datetime import date
from html.parser import HTMLParser
from typing import Any, Iterable, Mapping, Sequence
from urllib.parse import parse_qs, urljoin, urlparse

import httpx

from strict_common.ids import sha256_text, utc_now


_SEARCH_URL = "https://www.sogou.com/web"
_SO_SEARCH_URL = "https://www.so.com/s"
_BING_SEARCH_URL = "https://cn.bing.com/search"
_REDIRECT_ORIGIN = "https://www.sogou.com"
_MAX_RESPONSE_BYTES = 2 * 1024 * 1024
_MAX_QUERY_CHARS = 180
_MAX_SUMMARY_CHARS = 800
_MAX_OFFICIAL_PAGE_TEXT_CHARS = 12_000
_MAX_OFFICIAL_PAGES = 36
_USER_AGENT = "Mozilla/5.0 (compatible; YiyuThinkTankStrict/1.0)"
_ALLOWED_PROVIDER_HOSTS = frozenset(
    {
        "www.sogou.com",
        "sogou.com",
        "www.so.com",
        "so.com",
        "cn.bing.com",
        "www.bing.com",
        "bing.com",
    }
)
_POSITIVE_TERMS = (
    "获奖",
    "合作",
    "启动",
    "发布",
    "增长",
    "提升",
    "支持",
    "创新",
    "成效",
    "进展",
)
_NEGATIVE_TERMS = (
    "投诉",
    "质疑",
    "风险",
    "违规",
    "处罚",
    "争议",
    "失败",
    "危机",
    "造假",
    "下滑",
)

_OFFICIAL_CORE_ROUTE_TERMS: tuple[tuple[int, tuple[str, ...]], ...] = (
    (0, ("about", "about-us", "关于", "机构介绍", "机构简介", "organization-profile")),
    (1, ("team", "people", "团队", "成员", "理事", "治理", "governance")),
    (2, ("project", "program", "service", "项目", "业务", "服务", "计划")),
    (3, ("mission", "vision", "history", "使命", "愿景", "历程", "大事记")),
    (4, ("contact", "联系", "workbench", "产品")),
    (8, ("report", "article", "news", "报告", "文章", "资讯", "洞察")),
)


class PublicCaptureError(RuntimeError):
    def __init__(self, code: str, message: str, *, retryable: bool = True):
        super().__init__(message)
        self.code = code
        self.message = message
        self.retryable = retryable


@dataclass(frozen=True)
class PublicCaptureItem:
    title: str
    summary: str
    source_name: str
    source_url: str
    captured_at: str
    published_at: str | None
    sentiment: str
    sentiment_reason: str
    content_hash: str
    body_excerpt: str = ""
    body_fetched: bool = False

    def as_cloud_payload(self) -> dict[str, Any]:
        return {
            "title": self.title,
            "summary": self.summary,
            "sourceName": self.source_name,
            "sourceUrl": self.source_url,
            "capturedAt": self.captured_at,
            "publishedAt": self.published_at,
            "sentiment": self.sentiment,
            "sentimentReason": self.sentiment_reason,
            "contentHash": self.content_hash,
            "bodyExcerpt": self.body_excerpt,
            "bodyFetched": self.body_fetched,
        }


@dataclass(frozen=True)
class OfficialWebsitePage:
    title: str
    url: str
    text: str
    content_hash: str
    captured_at: str
    discovered_url: str = ""
    canonical_public_url: str = ""
    page_role: str = "unknown"
    capture_kind: str = "static"

    def as_cloud_payload(self) -> dict[str, Any]:
        return {
            "title": self.title,
            "url": self.url,
            "text": self.text,
            "contentHash": self.content_hash,
            "capturedAt": self.captured_at,
            "discoveredUrl": self.discovered_url or self.url,
            "canonicalPublicUrl": self.canonical_public_url,
            "pageRole": self.page_role,
            "captureKind": self.capture_kind,
        }


class _OfficialPageParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._skip_depth = 0
        self._title_depth = 0
        self.title_parts: list[str] = []
        self.text_parts: list[str] = []
        self.links: list[str] = []
        self.canonical_hints: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attributes = {str(key).casefold(): value for key, value in attrs}
        if tag in {"script", "style", "noscript", "svg"}:
            self._skip_depth += 1
            return
        if tag == "title":
            self._title_depth += 1
        if tag == "a" and not self._skip_depth:
            href = attributes.get("href")
            if href:
                self.links.append(href)
        if tag == "link" and "canonical" in str(attributes.get("rel") or "").casefold():
            href = attributes.get("href")
            if href:
                self.canonical_hints.append(href)
        if tag == "meta" and str(attributes.get("property") or "").casefold() == "og:url":
            content = attributes.get("content")
            if content:
                self.canonical_hints.append(content)

    def handle_endtag(self, tag: str) -> None:
        if tag in {"script", "style", "noscript", "svg"} and self._skip_depth:
            self._skip_depth -= 1
            return
        if tag == "title" and self._title_depth:
            self._title_depth -= 1

    def handle_data(self, data: str) -> None:
        if self._skip_depth:
            return
        value = " ".join(data.split())
        if not value:
            return
        self.text_parts.append(value)
        if self._title_depth:
            self.title_parts.append(value)


@dataclass
class _ParsedResult:
    title_parts: list[str]
    tail_parts: list[str]
    href: str | None = None


class _SogouResultParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._heading_depth = 0
        self._skip_depth = 0
        self._current: _ParsedResult | None = None
        self.results: list[_ParsedResult] = []

    def _finish_current(self) -> None:
        if self._current is not None:
            self.results.append(self._current)
        self._current = None

    def handle_starttag(
        self,
        tag: str,
        attrs: list[tuple[str, str | None]],
    ) -> None:
        if tag in {"script", "style"}:
            self._skip_depth += 1
            return
        if tag == "h3":
            self._finish_current()
            self._heading_depth = 1
            self._current = _ParsedResult([], [])
            return
        if self._heading_depth:
            self._heading_depth += 1
        if (
            self._current is not None
            and self._heading_depth
            and tag == "a"
            and self._current.href is None
        ):
            self._current.href = dict(attrs).get("href")

    def handle_endtag(self, tag: str) -> None:
        if tag in {"script", "style"} and self._skip_depth:
            self._skip_depth -= 1
            return
        if self._heading_depth:
            self._heading_depth -= 1

    def handle_data(self, data: str) -> None:
        if self._skip_depth or self._current is None:
            return
        target = (
            self._current.title_parts
            if self._heading_depth
            else self._current.tail_parts
        )
        target.append(data)

    def close(self) -> None:
        super().close()
        self._finish_current()


class _BingResultParser(HTMLParser):
    """Extract the ordinary Bing result title, destination and caption."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._result_depth = 0
        self._heading_depth = 0
        self._caption_depth = 0
        self._skip_depth = 0
        self._current: _ParsedResult | None = None
        self.results: list[_ParsedResult] = []

    def _finish_current(self) -> None:
        if self._current is not None:
            self.results.append(self._current)
        self._current = None
        self._result_depth = 0
        self._heading_depth = 0
        self._caption_depth = 0

    def handle_starttag(
        self,
        tag: str,
        attrs: list[tuple[str, str | None]],
    ) -> None:
        attributes = {str(key).casefold(): str(value or "") for key, value in attrs}
        classes = set(attributes.get("class", "").split())
        if tag in {"script", "style"}:
            self._skip_depth += 1
            return
        if tag == "li" and "b_algo" in classes:
            self._finish_current()
            self._current = _ParsedResult([], [])
            self._result_depth = 1
            return
        if self._current is None:
            return
        self._result_depth += 1
        if tag == "h2":
            self._heading_depth = 1
        elif self._heading_depth:
            self._heading_depth += 1
        if tag == "p" and not self._heading_depth:
            self._caption_depth = 1
        elif self._caption_depth:
            self._caption_depth += 1
        if tag == "a" and self._heading_depth and self._current.href is None:
            self._current.href = attributes.get("href") or None

    def handle_endtag(self, tag: str) -> None:
        if tag in {"script", "style"} and self._skip_depth:
            self._skip_depth -= 1
            return
        if self._current is None:
            return
        if self._heading_depth:
            self._heading_depth -= 1
        if self._caption_depth:
            self._caption_depth -= 1
        self._result_depth -= 1
        if self._result_depth <= 0:
            self._finish_current()

    def handle_data(self, data: str) -> None:
        if self._skip_depth or self._current is None:
            return
        if self._heading_depth:
            self._current.title_parts.append(data)
        elif self._caption_depth:
            self._current.tail_parts.append(data)

    def close(self) -> None:
        super().close()
        self._finish_current()


class _SoResultParser(HTMLParser):
    """Extract 360 Search result links and visible snippets."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._result_depth = 0
        self._heading_depth = 0
        self._caption_depth = 0
        self._skip_depth = 0
        self._current: _ParsedResult | None = None
        self.results: list[_ParsedResult] = []

    def _finish_current(self) -> None:
        if self._current is not None:
            self.results.append(self._current)
        self._current = None
        self._result_depth = 0
        self._heading_depth = 0
        self._caption_depth = 0

    def handle_starttag(
        self,
        tag: str,
        attrs: list[tuple[str, str | None]],
    ) -> None:
        attributes = {str(key).casefold(): str(value or "") for key, value in attrs}
        classes = set(attributes.get("class", "").split())
        if tag in {"script", "style"}:
            self._skip_depth += 1
            return
        if tag == "li" and "res-list" in classes:
            self._finish_current()
            self._current = _ParsedResult([], [])
            self._result_depth = 1
            return
        if self._current is None:
            return
        self._result_depth += 1
        if tag == "h3" and "res-title" in classes:
            self._heading_depth = 1
        elif self._heading_depth:
            self._heading_depth += 1
        if tag == "p" and "res-desc" in classes:
            self._caption_depth = 1
        elif self._caption_depth:
            self._caption_depth += 1
        if tag == "a" and self._heading_depth and self._current.href is None:
            self._current.href = attributes.get("data-mdurl") or attributes.get("href") or None

    def handle_endtag(self, tag: str) -> None:
        if tag in {"script", "style"} and self._skip_depth:
            self._skip_depth -= 1
            return
        if self._current is None:
            return
        if self._heading_depth:
            self._heading_depth -= 1
        if self._caption_depth:
            self._caption_depth -= 1
        self._result_depth -= 1
        if self._result_depth <= 0:
            self._finish_current()

    def handle_data(self, data: str) -> None:
        if self._skip_depth or self._current is None:
            return
        if self._heading_depth:
            self._current.title_parts.append(data)
        elif self._caption_depth:
            self._current.tail_parts.append(data)

    def close(self) -> None:
        super().close()
        self._finish_current()


def _compact_text(value: str, *, limit: int) -> str:
    normalized = " ".join(html.unescape(value).split())
    return normalized[:limit].strip()


def _official_page_priority(url: str, title: str = "") -> tuple[int, int, int, str]:
    """Put user-facing institutional pages ahead of crawl-only indexes/details."""

    parsed = urlparse(url)
    path = parsed.path.strip("/").casefold()
    route = " ".join(parse_qs(parsed.query).get("page", []))
    haystack = f"{route} {path} {title}".casefold()
    is_root = not path and not parsed.query
    if is_root:
        category = -1
    else:
        category = 12
        for rank, terms in _OFFICIAL_CORE_ROUTE_TERMS:
            if any(term.casefold() in haystack for term in terms):
                category = rank
                break
    # A site's real interactive route is a better user-facing citation than
    # its SEO mirror; share/detail pages remain useful evidence but are not
    # allowed to crowd out institutional entry pages.
    crawl_only = -1 if is_root else 2 if path.startswith("share/") else 1 if path.startswith("seo/") else 0
    depth = len([part for part in path.split("/") if part])
    return crawl_only, category, depth, url.casefold()


def _looks_like_transition_page(title: str, text: str) -> bool:
    sample = f"{title}\n{text}".casefold()
    return len(text) < 100 and any(
        marker in sample
        for marker in ("正在进入", "正在加载", "加载中", "即将跳转", "redirecting", "loading")
    )


def _official_page_role(url: str, title: str, text: str) -> str:
    """Classify a page by business role, never by one customer's route map."""

    parsed = urlparse(url)
    route = " ".join(parse_qs(parsed.query).get("page", []))
    heading = f"{route} {parsed.path} {title}".casefold()
    body = text[:600].casefold()
    if any(term in heading for term in ("登录", "注册", "工作台", "dashboard", "sign in", "task board")):
        return "product_demo"
    if any(term in heading for term in ("报告", "文章", "图书", "资源", "洞察", "news", "article", "report", "library")):
        return "resource"
    if any(term in heading for term in ("关于", "团队", "理事", "治理", "使命", "愿景", "历程", "about", "team", "people", "governance", "history")):
        return "institutional_profile"
    if any(term in heading for term in ("项目", "计划", "服务", "课程", "学院", "project", "program", "service")):
        return "project_service"
    if any(term in heading for term in ("成果", "成效", "影响", "覆盖", "受益", "impact", "outcome")):
        return "impact"
    if any(term in body for term in ("使命", "愿景", "理事会", "治理结构", "团队成员")):
        return "institutional_profile"
    if any(term in body for term in ("项目目标", "服务对象", "实施方式", "课程体系")):
        return "project_service"
    if any(term in body for term in ("项目成果", "覆盖地区", "受益人数", "成效")):
        return "impact"
    return "unknown"


def _capture_kind(url: str, *, rendered: bool = False) -> str:
    path = urlparse(url).path.strip("/").casefold()
    if path.startswith("seo/") or "sitemap" in path:
        return "seo_mirror"
    return "rendered" if rendered else "static"


def _canonical_public_url(
    capture_url: str,
    hints: Iterable[str],
    *,
    root_host: str,
) -> str:
    """Return a verified user-facing URL, or empty when only a crawl mirror exists."""

    candidates = [urljoin(capture_url, str(value or "")) for value in hints]
    if _capture_kind(capture_url) != "seo_mirror":
        candidates.append(capture_url)
    for candidate in candidates:
        public = _public_url(candidate)
        if not public:
            continue
        parsed = urlparse(public)
        host = str(parsed.hostname or "").removeprefix("www.").lower()
        if host != root_host or _capture_kind(public) == "seo_mirror":
            continue
        return public
    return ""


def _public_url(value: str) -> str | None:
    parsed = urlparse(value.strip())
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        return None
    hostname = parsed.hostname.rstrip(".").lower()
    if hostname in {"localhost", "localhost.localdomain"}:
        return None
    try:
        address = ipaddress.ip_address(hostname)
    except ValueError:
        return value.strip()
    if (
        address.is_private
        or address.is_loopback
        or address.is_link_local
        or address.is_multicast
        or address.is_reserved
        or address.is_unspecified
    ):
        return None
    return value.strip()


def _assert_public_network_url(value: str) -> str:
    normalized = _public_url(value)
    if normalized is None:
        raise PublicCaptureError(
            "official_website_url_invalid",
            "官网地址必须是可公开访问的 HTTP 或 HTTPS 地址",
            retryable=False,
        )
    host = str(urlparse(normalized).hostname or "")
    try:
        addresses = {
            item[4][0]
            for item in socket.getaddrinfo(host, None, type=socket.SOCK_STREAM)
        }
    except socket.gaierror as exc:
        raise PublicCaptureError(
            "official_website_dns_failed",
            "官网域名暂时无法解析，请稍后重试",
        ) from exc
    for value in addresses:
        address = ipaddress.ip_address(value)
        if (
            address.is_private
            or address.is_loopback
            or address.is_link_local
            or address.is_multicast
            or address.is_reserved
            or address.is_unspecified
        ):
            raise PublicCaptureError(
                "official_website_private_network_rejected",
                "官网地址不能指向本机或内网地址",
                retryable=False,
            )
    return normalized


def _official_page_text(response: httpx.Response) -> str:
    if response.status_code >= 400:
        raise PublicCaptureError(
            "official_website_http_failed",
            f"官网页面暂时不可用（HTTP {response.status_code}）",
        )
    content_type = str(response.headers.get("content-type") or "").lower()
    if "html" not in content_type and "text/" not in content_type:
        raise PublicCaptureError(
            "official_website_content_unsupported",
            "官网页面不是可读取的网页文本",
            retryable=False,
        )
    if len(response.content) > _MAX_RESPONSE_BYTES:
        raise PublicCaptureError(
            "official_website_response_too_large",
            "官网页面超过安全读取大小限制",
            retryable=False,
        )
    return response.text


def _official_get(client: httpx.Client, url: str) -> tuple[str, httpx.Response]:
    current = _assert_public_network_url(url)
    for _ in range(4):
        try:
            response = client.get(
                current,
                headers={"User-Agent": _USER_AGENT, "Accept": "text/html, text/plain"},
            )
        except httpx.HTTPError as exc:
            raise PublicCaptureError(
                "official_website_unreachable",
                "官网暂时无法连接，请稍后重试",
            ) from exc
        if response.status_code not in {301, 302, 303, 307, 308}:
            return current, response
        target = response.headers.get("location")
        if not target:
            break
        current = _assert_public_network_url(urljoin(current, target))
    raise PublicCaptureError(
        "official_website_redirect_invalid",
        "官网重定向次数过多或目标无效",
        retryable=False,
    )


def _optional_sitemap_urls(
    client: httpx.Client,
    *,
    root_url: str,
    root_host: str,
) -> list[str]:
    """Discover same-origin pages from robots.txt and bounded XML sitemaps.

    Sitemap discovery is an optional lane: malformed or unavailable sitemap
    files never make an otherwise-readable website fail.  It is essential for
    SPA homepages whose server-rendered transition HTML contains no real links.
    """

    parsed_root = urlparse(root_url)
    origin = f"{parsed_root.scheme}://{parsed_root.netloc}"
    sitemap_queue = [urljoin(origin, "/sitemap.xml")]
    try:
        _, robots_response = _official_get(client, urljoin(origin, "/robots.txt"))
        if robots_response.status_code < 400 and len(robots_response.content) <= _MAX_RESPONSE_BYTES:
            for line in robots_response.text.splitlines():
                match = re.match(r"\s*Sitemap\s*:\s*(\S+)", line, re.IGNORECASE)
                if match:
                    sitemap_queue.insert(0, match.group(1).strip())
    except PublicCaptureError:
        pass

    pages: list[str] = []
    seen_sitemaps: set[str] = set()
    while sitemap_queue and len(seen_sitemaps) < 6 and len(pages) < 240:
        raw_sitemap = sitemap_queue.pop(0)
        try:
            sitemap_url = _assert_public_network_url(raw_sitemap)
        except PublicCaptureError:
            continue
        parsed_sitemap = urlparse(sitemap_url)
        sitemap_host = str(parsed_sitemap.hostname or "").removeprefix("www.").lower()
        if sitemap_host != root_host:
            continue
        sitemap_key = sitemap_url.rstrip("/").casefold()
        if sitemap_key in seen_sitemaps:
            continue
        seen_sitemaps.add(sitemap_key)
        try:
            _, response = _official_get(client, sitemap_url)
        except PublicCaptureError:
            continue
        if response.status_code >= 400 or len(response.content) > _MAX_RESPONSE_BYTES:
            continue
        try:
            document = ET.fromstring(response.content)
        except ET.ParseError:
            continue
        root_kind = document.tag.rsplit("}", 1)[-1].casefold()
        locations = [
            str(node.text or "").strip()
            for node in document.iter()
            if node.tag.rsplit("}", 1)[-1].casefold() == "loc" and str(node.text or "").strip()
        ]
        if root_kind == "sitemapindex":
            sitemap_queue.extend(locations[:12])
            continue
        for location in locations:
            candidate = _public_url(location.split("#", 1)[0])
            if not candidate:
                continue
            parsed = urlparse(candidate)
            candidate_host = str(parsed.hostname or "").removeprefix("www.").lower()
            if candidate_host != root_host:
                continue
            if re.search(r"\.(?:jpg|jpeg|png|gif|svg|pdf|zip|docx?|xlsx?|xml)(?:$|\?)", parsed.path, re.I):
                continue
            if candidate not in pages:
                pages.append(candidate)
            if len(pages) >= 240:
                break
    return pages


def capture_official_website(
    url: str,
    *,
    max_pages: int = _MAX_OFFICIAL_PAGES,
    transport: httpx.BaseTransport | None = None,
) -> list[OfficialWebsitePage]:
    """Read a bounded same-origin official site; never uses a search provider."""

    root_url = _assert_public_network_url(url)
    root_host = str(urlparse(root_url).hostname or "").removeprefix("www.").lower()
    bounded_max = min(max(int(max_pages), 1), _MAX_OFFICIAL_PAGES)
    captured_at = utc_now()
    queue = [root_url]
    seen: set[str] = set()
    seen_page_bodies: set[str] = set()
    pages: list[OfficialWebsitePage] = []
    with httpx.Client(
        timeout=httpx.Timeout(12.0, connect=5.0),
        follow_redirects=False,
        trust_env=False,
        transport=transport,
    ) as client:
        sitemap_candidates = _optional_sitemap_urls(
            client,
            root_url=root_url,
            root_host=root_host,
        )
        for candidate in sorted(sitemap_candidates, key=_official_page_priority):
            if candidate.rstrip("/").lower() != root_url.rstrip("/").lower():
                queue.append(candidate)
        while queue and len(pages) < bounded_max:
            requested = queue.pop(0)
            dedupe = requested.rstrip("/").lower()
            if dedupe in seen:
                continue
            seen.add(dedupe)
            final_url, response = _official_get(client, requested)
            text = _official_page_text(response)
            parser = _OfficialPageParser()
            parser.feed(text)
            parser.close()
            title = _compact_text(" ".join(parser.title_parts), limit=300)
            visible = _compact_text(
                " ".join(parser.text_parts),
                limit=_MAX_OFFICIAL_PAGE_TEXT_CHARS,
            )
            if not title:
                title = str(urlparse(final_url).path.rstrip("/").split("/")[-1] or root_host)
            body_fingerprint = sha256_text(f"{title}\n{visible}")
            if (
                visible
                and not _looks_like_transition_page(title, visible)
                and body_fingerprint not in seen_page_bodies
            ):
                seen_page_bodies.add(body_fingerprint)
                pages.append(
                    OfficialWebsitePage(
                        title=title,
                        url=final_url,
                        text=visible,
                        content_hash=sha256_text(f"{title}\n{visible}\n{final_url}"),
                        captured_at=captured_at,
                        discovered_url=requested,
                        canonical_public_url=_canonical_public_url(
                            final_url,
                            parser.canonical_hints,
                            root_host=root_host,
                        ),
                        page_role=_official_page_role(final_url, title, visible),
                        capture_kind=_capture_kind(final_url),
                    )
                )
            candidates: list[str] = []
            for href in parser.links:
                candidate = _public_url(urljoin(final_url, html.unescape(href)).split("#", 1)[0])
                if not candidate:
                    continue
                parsed = urlparse(candidate)
                candidate_host = str(parsed.hostname or "").removeprefix("www.").lower()
                if candidate_host != root_host:
                    continue
                if re.search(r"\.(?:jpg|jpeg|png|gif|svg|pdf|zip|docx?|xlsx?)(?:$|\?)", parsed.path, re.I):
                    continue
                candidates.append(candidate)
            # Stable breadth-first order keeps top-level/about/project pages ahead
            # of deep news detail pages, while still retaining exact page URLs.
            for candidate in sorted(dict.fromkeys(candidates), key=_official_page_priority):
                if candidate.rstrip("/").lower() not in seen and candidate not in queue:
                    queue.append(candidate)
    if not pages:
        raise PublicCaptureError(
            "official_website_empty",
            "官网没有返回可读取的网页内容",
        )
    return pages


def merge_rendered_official_pages(
    root_url: str,
    static_pages: Sequence[OfficialWebsitePage],
    rendered_pages: Sequence[Mapping[str, Any]],
    *,
    max_pages: int = _MAX_OFFICIAL_PAGES,
) -> list[OfficialWebsitePage]:
    """Prefer a rendered DOM snapshot for the same canonical page URL.

    Renderer output is treated as untrusted input: URLs are revalidated,
    restricted to the registered origin, text is bounded, and hashes are
    recomputed locally before anything can be sent to the organization cloud.
    """

    normalized_root = _assert_public_network_url(root_url)
    root_host = str(urlparse(normalized_root).hostname or "").removeprefix("www.").lower()
    by_url: dict[str, OfficialWebsitePage] = {
        page.url.rstrip("/").casefold(): page for page in static_pages
    }
    order = [page.url.rstrip("/").casefold() for page in static_pages]
    rendered_order: list[str] = []
    for raw in list(rendered_pages)[:16]:
        try:
            page_url = _assert_public_network_url(str(raw.get("url") or ""))
        except PublicCaptureError:
            continue
        host = str(urlparse(page_url).hostname or "").removeprefix("www.").lower()
        if host != root_host:
            continue
        title = _compact_text(str(raw.get("title") or ""), limit=300)
        text = _compact_text(str(raw.get("text") or ""), limit=_MAX_OFFICIAL_PAGE_TEXT_CHARS)
        if not text:
            continue
        if not title:
            title = str(urlparse(page_url).path.rstrip("/").split("/")[-1] or root_host)
        page = OfficialWebsitePage(
            title=title,
            url=page_url,
            text=text,
            content_hash=sha256_text(f"{title}\n{text}\n{page_url}"),
            captured_at=str(raw.get("capturedAt") or utc_now()),
            discovered_url=str(raw.get("discoveredUrl") or page_url),
            canonical_public_url=_canonical_public_url(
                page_url,
                [str(raw.get("canonicalPublicUrl") or "")],
                root_host=root_host,
            ),
            page_role=_official_page_role(page_url, title, text),
            capture_kind=_capture_kind(page_url, rendered=True),
        )
        key = page_url.rstrip("/").casefold()
        if key not in rendered_order:
            rendered_order.append(key)
        current = by_url.get(key)
        if current is None:
            order.append(key)
            by_url[key] = page
        elif len(page.text) > len(current.text):
            by_url[key] = page
    bounded_max = min(max(int(max_pages), 1), _MAX_OFFICIAL_PAGES)
    prioritized = list(dict.fromkeys([*rendered_order, *order]))
    rendered_keys = set(rendered_order)
    semantic_routes: set[str] = set()
    selected: list[OfficialWebsitePage] = []
    for key in sorted(
        prioritized,
        key=lambda item: (
            0 if item in rendered_keys else 1,
            _official_page_priority(by_url[item].url, by_url[item].title),
            prioritized.index(item),
        ),
    ):
        page = by_url[key]
        parsed = urlparse(page.url)
        route = " ".join(parse_qs(parsed.query).get("page", [])).casefold()
        path_token = parsed.path.strip("/").casefold()
        semantic = route or (path_token.removeprefix("seo/").rstrip("s") if path_token.startswith("seo/") else "")
        # When a rendered, user-facing page and an SEO mirror describe the same
        # major entry, keep the real route in the current capture set.
        if semantic in {"about", "home", "report", "report-library", "article"}:
            normalized_semantic = "report" if semantic in {"report", "report-library"} else semantic
            if normalized_semantic in semantic_routes and parsed.path.startswith("/seo/"):
                continue
            semantic_routes.add(normalized_semantic)
        if _looks_like_transition_page(page.title, page.text):
            continue
        selected.append(page)
        if len(selected) >= bounded_max:
            break
    return selected


def _provider_response_text(response: httpx.Response) -> str:
    if response.status_code >= 400:
        raise PublicCaptureError(
            "public_search_http_failed",
            f"公开搜索服务暂时不可用（HTTP {response.status_code}）",
        )
    content = response.content
    if len(content) > _MAX_RESPONSE_BYTES:
        raise PublicCaptureError(
            "public_search_response_too_large",
            "公开搜索响应超过安全大小限制",
            retryable=False,
        )
    for encoding in ("utf-8", "gb18030"):
        try:
            return content.decode(encoding)
        except UnicodeDecodeError:
            continue
    return content.decode("utf-8", errors="replace")


def _provider_get(
    client: httpx.Client,
    url: str,
    *,
    params: dict[str, str] | None = None,
) -> httpx.Response:
    parsed = urlparse(url)
    if parsed.scheme != "https" or parsed.hostname not in _ALLOWED_PROVIDER_HOSTS:
        raise PublicCaptureError(
            "public_search_provider_invalid",
            "公开搜索仅允许访问内置检索服务",
            retryable=False,
        )
    try:
        return client.get(
            url,
            params=params,
            headers={"User-Agent": _USER_AGENT, "Accept": "text/html"},
        )
    except httpx.HTTPError as exc:
        raise PublicCaptureError(
            "public_search_unreachable",
            "公开搜索服务暂时无法连接，请稍后重试",
        ) from exc


def _resolve_result_url(
    client: httpx.Client,
    raw_href: str,
) -> str | None:
    absolute = urljoin(_REDIRECT_ORIGIN, html.unescape(raw_href.strip()))
    parsed = urlparse(absolute)
    if parsed.hostname not in _ALLOWED_PROVIDER_HOSTS:
        return _public_url(absolute)
    if not parsed.path.startswith("/link"):
        return None
    redirect_page = _provider_response_text(_provider_get(client, absolute))
    match = re.search(
        r"""window\.location\.replace\((".*?")\)""",
        redirect_page,
        flags=re.DOTALL,
    )
    if match is None:
        match = re.search(
            r"""URL=['"]([^'"]+)['"]""",
            redirect_page,
            flags=re.IGNORECASE,
        )
        target = html.unescape(match.group(1)) if match else ""
    else:
        try:
            target = str(json.loads(match.group(1)))
        except (TypeError, ValueError, json.JSONDecodeError):
            target = ""
    return _public_url(target)


def _sentiment(title: str, summary: str) -> tuple[str, str]:
    content = f"{title} {summary}"
    negative = [term for term in _NEGATIVE_TERMS if term in content]
    positive = [term for term in _POSITIVE_TERMS if term in content]
    if negative and len(negative) >= len(positive):
        return "negative", f"公开摘要命中风险词：{'、'.join(negative[:3])}"
    if positive:
        return "positive", f"公开摘要命中进展词：{'、'.join(positive[:3])}"
    return "neutral", "未命中明确正负向词，保持中性"


def _queries(
    query: str,
    preferred_sources: Iterable[dict[str, Any]],
) -> list[str]:
    base = _compact_text(query, limit=_MAX_QUERY_CHARS)
    values = [base] if base else []
    for source in preferred_sources:
        url = _public_url(str(source.get("url") or ""))
        if not url:
            continue
        hostname = urlparse(url).hostname or ""
        values.append(
            _compact_text(f"{base} site:{hostname}", limit=_MAX_QUERY_CHARS)
        )
    return list(dict.fromkeys(value for value in values if value))[:4]


def capture_public_web(
    query: str,
    *,
    max_results: int = 5,
    preferred_sources: Sequence[dict[str, Any]] = (),
    transport: httpx.BaseTransport | None = None,
) -> list[PublicCaptureItem]:
    queries = _queries(query, preferred_sources)
    if not queries:
        raise PublicCaptureError(
            "public_search_query_required",
            "公开搜索需要明确的检索对象",
            retryable=False,
        )
    bounded_max = min(max(int(max_results), 1), 10)
    captured_at = utc_now()
    results: list[PublicCaptureItem] = []
    seen: set[str] = set()
    last_provider_error: PublicCaptureError | None = None
    provider_responded = False
    with httpx.Client(
        timeout=httpx.Timeout(12.0, connect=5.0),
        follow_redirects=False,
        trust_env=False,
        transport=transport,
    ) as client:
        for search_query in queries:
            # Sogou is retained as the first provider, but it frequently sends
            # unattended clients to an anti-spider page. A query that yields no
            # usable public result therefore falls through to Bing instead of
            # being reported as a successful empty research run.
            providers: tuple[tuple[str, str, dict[str, str]], ...] = (
                ("sogou", _SEARCH_URL, {"query": search_query}),
                ("so", _SO_SEARCH_URL, {"q": search_query}),
                ("bing", _BING_SEARCH_URL, {"q": search_query, "setlang": "zh-Hans"}),
            )
            for provider_name, provider_url, params in providers:
                before_count = len(results)
                try:
                    response = _provider_get(client, provider_url, params=params)
                    if response.status_code in {301, 302, 303, 307, 308}:
                        continue
                    page = _provider_response_text(response)
                    provider_responded = True
                except PublicCaptureError as exc:
                    last_provider_error = exc
                    continue
                if provider_name == "sogou":
                    if "antispider" in page.casefold() or "请输入验证码" in page:
                        continue
                    parser: _SogouResultParser | _SoResultParser | _BingResultParser = _SogouResultParser()
                elif provider_name == "so":
                    parser = _SoResultParser()
                else:
                    parser = _BingResultParser()
                parser.feed(page)
                parser.close()
                for parsed in parser.results:
                    title = _compact_text("".join(parsed.title_parts), limit=240)
                    if not title or not parsed.href:
                        continue
                    tail = _compact_text("".join(parsed.tail_parts), limit=1600)
                    summary = _compact_text(
                        tail.split("推荐您搜索", 1)[0],
                        limit=_MAX_SUMMARY_CHARS,
                    ) or title
                    source_url = (
                        _resolve_result_url(client, parsed.href)
                        if provider_name == "sogou"
                        else _public_url(html.unescape(parsed.href.strip()))
                    )
                    if not source_url:
                        continue
                    source_host = (urlparse(source_url).hostname or "").casefold()
                    if source_host in _ALLOWED_PROVIDER_HOSTS:
                        continue
                    dedupe_key = source_url.lower().rstrip("/")
                    if dedupe_key in seen:
                        continue
                    seen.add(dedupe_key)
                    hostname = source_host.removeprefix("www.")
                    sentiment, sentiment_reason = _sentiment(title, summary)
                    content_hash = sha256_text("\n".join((title, summary, source_url)))
                    results.append(
                        PublicCaptureItem(
                            title=title,
                            summary=summary,
                            source_name=hostname,
                            source_url=source_url,
                            captured_at=captured_at,
                            published_at=_published_date(f"{title}\n{summary}"),
                            sentiment=sentiment,
                            sentiment_reason=sentiment_reason,
                            content_hash=content_hash,
                        )
                    )
                    if len(results) >= bounded_max:
                        return results
                if len(results) > before_count:
                    break
    if not results and not provider_responded and last_provider_error is not None:
        raise last_provider_error
    return results


_PUBLIC_DATE_PATTERNS = (
    re.compile(r"\b(20\d{2})[-/.](0?[1-9]|1[0-2])[-/.]([0-2]?\d|3[01])\b"),
    re.compile(r"\b(20\d{2})年(0?[1-9]|1[0-2])月([0-2]?\d|3[01])日"),
)


def _published_date(text: str) -> str | None:
    """Return a conservative publication date found in visible page text."""

    for pattern in _PUBLIC_DATE_PATTERNS:
        match = pattern.search(text[:4_000])
        if match is None:
            continue
        year, month, day = (int(value) for value in match.groups())
        try:
            return date(year, month, day).isoformat()
        except ValueError:
            continue
    return None


def enrich_public_capture_item(
    item: PublicCaptureItem,
    *,
    transport: httpx.BaseTransport | None = None,
) -> PublicCaptureItem:
    """Read one public result page without turning it into a second authority.

    Search snippets remain usable when a site blocks direct reading.  A
    successfully read body is only an evidence excerpt for the research judge;
    the canonical public URL remains the source locator.
    """

    try:
        with httpx.Client(
            timeout=httpx.Timeout(12.0, connect=5.0),
            follow_redirects=False,
            trust_env=False,
            transport=transport,
        ) as client:
            final_url, response = _official_get(client, item.source_url)
            page = _official_page_text(response)
    except PublicCaptureError:
        return item
    parser = _OfficialPageParser()
    try:
        parser.feed(page)
        parser.close()
    except (ValueError, AssertionError):
        return item
    body = _compact_text(" ".join(parser.text_parts), limit=4_000)
    if len(body) < 40 or _looks_like_transition_page(
        _compact_text(" ".join(parser.title_parts), limit=240), body
    ):
        return item
    published_at = item.published_at or _published_date(body)
    return replace(
        item,
        source_url=final_url,
        published_at=published_at,
        body_excerpt=body,
        body_fetched=True,
        content_hash=sha256_text("\n".join((item.title, body, final_url))),
    )

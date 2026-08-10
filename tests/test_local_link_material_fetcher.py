from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest

from backend.app import link_material_fetcher as fetcher


@pytest.fixture(autouse=True)
def public_dns(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        fetcher.socket,
        "getaddrinfo",
        lambda *_args, **_kwargs: [
            (2, 1, 6, "", ("8.8.8.8", 443)),
        ],
    )


def test_detects_bv_and_share_copy_but_rejects_insecure_urls() -> None:
    url, platform = fetcher._validated_url("BV1ab411c7d9")  # noqa: SLF001
    assert url == "https://www.bilibili.com/video/BV1ab411c7d9"
    assert platform == "bilibili"

    url, platform = fetcher._validated_url(  # noqa: SLF001
        "复制口令后打开 https://xhslink.com/aBc123 查看笔记"
    )
    assert url == "https://xhslink.com/aBc123"
    assert platform == "xiaohongshu"

    with pytest.raises(fetcher.LinkMaterialFetchError) as blocked:
        fetcher._validated_url(  # noqa: SLF001
            "http://www.bilibili.com/video/BV1ab411c7d9"
        )
    assert blocked.value.state == "blocked"
    assert blocked.value.retryable is False


def test_ssrf_rejects_private_dns_resolution(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        fetcher.socket,
        "getaddrinfo",
        lambda *_args, **_kwargs: [
            (2, 1, 6, "", ("127.0.0.1", 443)),
        ],
    )
    with pytest.raises(fetcher.LinkMaterialFetchError) as blocked:
        fetcher._validated_url(  # noqa: SLF001
            "https://www.bilibili.com/video/BV1ab411c7d9"
        )
    assert blocked.value.code == "link_import_private_address_forbidden"
    assert blocked.value.state == "blocked"


def test_media_links_reject_generic_paths_and_auth_is_blocked() -> None:
    with pytest.raises(fetcher.LinkMaterialFetchError) as blocked:
        fetcher._validated_media_content_url(  # noqa: SLF001
            "https://www.bilibili.com/account/login?next=https://127.0.0.1",
            "bilibili",
        )
    assert blocked.value.code == "link_import_content_path_unsupported"
    assert blocked.value.state == "blocked"

    with pytest.raises(fetcher.LinkMaterialFetchError) as blocked:
        fetcher._validated_media_content_url(  # noqa: SLF001
            "https://www.xiaohongshu.com/user/profile/example",
            "xiaohongshu",
        )
    assert blocked.value.code == "link_import_content_path_unsupported"

    error = fetcher._response_error(httpx.Response(403))  # noqa: SLF001
    assert error.code == "link_import_authentication_required"
    assert error.status_code == 409
    assert error.state == "blocked"
    assert error.retryable is False


def test_wechat_extracts_only_published_article_body(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        fetcher,
        "_fetch_public_html",
        lambda *_args: (
            "https://mp.weixin.qq.com/s/example",
            "text/html; charset=utf-8",
            """
            <html><head><meta property="og:title" content="儿童项目进展"></head>
            <body>
              <div class="navigation">导航不应进入正文</div>
              <h2 class="rich_media_title" id="activity-name">儿童项目进展</h2>
              <span id="js_name">日慈公益</span>
              <div id="js_content" class="rich_media_content">
                <p>第一段项目事实。</p>
                <script>SECRET_SCRIPT</script>
                <section>第二段行动计划。</section>
              </div>
              <div>页脚不应进入正文</div>
            </body></html>
            """,
        ),
    )
    result = fetcher.fetch_link_material(
        "https://mp.weixin.qq.com/s/example"
    )
    assert result["title"] == "儿童项目进展"
    assert "作者：日慈公益" in result["text"]
    assert "第一段项目事实。" in result["text"]
    assert "第二段行动计划。" in result["text"]
    assert "导航不应进入正文" not in result["text"]
    assert "页脚不应进入正文" not in result["text"]
    assert "SECRET_SCRIPT" not in result["text"]
    assert result["metadata"]["transcriptSource"] == "article_html"
    assert result["metadata"]["mediaCacheStatus"] == "not_downloaded"


def test_bilibili_prefers_subtitles_and_cleans_temporary_media(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fake_download(
        url: str,
        destination: Path,
        **_kwargs: object,
    ) -> fetcher._MediaDownload:  # noqa: SLF001
        assert url.endswith("BV1ab411c7d9")
        subtitle = destination / "video.zh.vtt"
        subtitle.write_text(
            "WEBVTT\n\n00:00:00.000 --> 00:00:01.000\n项目背景\n"
            "\n00:00:01.000 --> 00:00:02.000\n项目背景\n"
            "\n00:00:02.000 --> 00:00:03.000\n下一步行动\n",
            encoding="utf-8",
        )
        media = destination / "video.m4a"
        media.write_bytes(b"temporary-media")
        return fetcher._MediaDownload(  # noqa: SLF001
            title="日慈项目访谈",
            description="公开简介",
            source_url=url,
            media_path=media,
            subtitle_paths=(subtitle,),
            duration_seconds=180.0,
        )

    monkeypatch.setattr(fetcher, "_download_public_media", fake_download)
    monkeypatch.setattr(
        fetcher,
        "transcribe_recording",
        lambda *_args, **_kwargs: pytest.fail("有字幕时不应调用 ASR"),
    )
    result = fetcher.fetch_link_material(
        "BV1ab411c7d9",
        data_root=tmp_path,
    )
    assert result["title"] == "日慈项目访谈"
    assert result["text"] == "公开简介\n\n项目背景\n下一步行动"
    assert result["metadata"]["transcriptSource"] == "platform_subtitle"
    assert result["metadata"]["mediaCacheStatus"] == "cleaned"
    assert list((tmp_path / "tmp" / "link-material").iterdir()) == []


def test_xiaohongshu_audio_falls_back_to_device_local_asr(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fake_download(
        url: str,
        destination: Path,
        **_kwargs: object,
    ) -> fetcher._MediaDownload:  # noqa: SLF001
        media = destination / "note.m4a"
        media.write_bytes(b"temporary-media")
        return fetcher._MediaDownload(  # noqa: SLF001
            title="项目访谈",
            description="",
            source_url=url,
            media_path=media,
            subtitle_paths=(),
            duration_seconds=60.0,
        )

    def fake_asr(model_root: Path, audio_path: str) -> SimpleNamespace:
        assert model_root == tmp_path / "models"
        assert Path(audio_path).read_bytes() == b"temporary-media"
        return SimpleNamespace(
            dialogue_text="说话人A：这是本机转写正文",
            result=SimpleNamespace(text="这是本机转写正文"),
        )

    monkeypatch.setattr(fetcher, "_download_public_media", fake_download)
    monkeypatch.setattr(fetcher, "transcribe_recording", fake_asr)
    result = fetcher.fetch_link_material(
        "https://www.xiaohongshu.com/explore/example123",
        data_root=tmp_path,
    )
    assert result["text"] == "说话人A：这是本机转写正文"
    assert result["metadata"]["transcriptSource"] == "local_asr"
    assert list((tmp_path / "tmp" / "link-material").iterdir()) == []


def test_missing_local_asr_model_is_accurately_blocked(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fake_download(
        url: str,
        destination: Path,
        **_kwargs: object,
    ) -> fetcher._MediaDownload:  # noqa: SLF001
        media = destination / "video.m4a"
        media.write_bytes(b"temporary-media")
        return fetcher._MediaDownload(  # noqa: SLF001
            title="无字幕视频",
            description="",
            source_url=url,
            media_path=media,
            subtitle_paths=(),
            duration_seconds=10.0,
        )

    monkeypatch.setattr(fetcher, "_download_public_media", fake_download)
    monkeypatch.setattr(
        fetcher,
        "transcribe_recording",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            RuntimeError("本机 ASR 模型未就绪，请先在系统设置中下载")
        ),
    )
    with pytest.raises(fetcher.LinkMaterialFetchError) as blocked:
        fetcher.fetch_link_material(
            "https://www.bilibili.com/video/BV1ab411c7d9",
            data_root=tmp_path,
        )
    assert blocked.value.code == "link_import_asr_model_missing"
    assert blocked.value.state == "blocked"
    assert blocked.value.retryable is False
    assert list((tmp_path / "tmp" / "link-material").iterdir()) == []

from __future__ import annotations

import httpx

from backend.app import intelligence_capture_local
from backend.app.intelligence_capture_local import (
    PublicCaptureItem,
    capture_official_website,
    capture_public_web,
    enrich_public_capture_item,
    merge_rendered_official_pages,
)
from backend.app.ui_domains.gc12_intelligence import (
    _judge_candidates,
    _research_plan,
)


def test_public_capture_reads_only_search_metadata_and_resolves_public_links() -> None:
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(str(request.url))
        if request.url.path == "/web":
            return httpx.Response(
                200,
                content=(
                    '<html><body><h3><a href="/link?url=one">'
                    "日慈公益启动儿童心理支持项目"
                    "</a></h3><p>项目已在三地启动，并发布阶段进展。</p>"
                    '<h3><a href="http://127.0.0.1/private">内网结果</a></h3>'
                    "<p>不应被采集。</p></body></html>"
                ).encode(),
            )
        assert request.url.path == "/link"
        return httpx.Response(
            200,
            content=(
                '<script>window.location.replace('
                '"https://example.org/rici-update")</script>'
            ).encode(),
        )

    results = capture_public_web(
        "日慈公益基金会",
        max_results=5,
        transport=httpx.MockTransport(handler),
    )

    assert len(results) == 1
    assert results[0].title == "日慈公益启动儿童心理支持项目"
    assert results[0].source_url == "https://example.org/rici-update"
    assert results[0].source_name == "example.org"
    assert results[0].sentiment == "positive"
    assert "项目已在三地启动" in results[0].summary
    assert all("127.0.0.1" not in item.source_url for item in results)
    assert len(calls) == 2


def test_preferred_source_is_used_as_search_constraint_not_direct_fetch() -> None:
    calls: list[httpx.URL] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.url)
        return httpx.Response(200, content=b"<html></html>")

    results = capture_public_web(
        "公益项目",
        preferred_sources=[
            {"url": "https://official.example/news", "label": "官网"},
            {"url": "http://localhost/private", "label": "非法来源"},
        ],
        transport=httpx.MockTransport(handler),
    )

    assert results == []
    assert len(calls) == 6
    assert {url.host for url in calls} == {"www.sogou.com", "www.so.com", "cn.bing.com"}
    assert any("site%3Aofficial.example" in str(url) for url in calls)


def test_public_capture_falls_back_to_360_when_sogou_is_blocked() -> None:
    calls: list[httpx.URL] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.url)
        if request.url.host == "www.sogou.com":
            return httpx.Response(
                302,
                headers={"location": "https://www.sogou.com/antispider/"},
            )
        assert request.url.host == "www.so.com"
        return httpx.Response(
            200,
            text=(
                '<ul><li class="res-list"><h3 class="res-title">'
                '<a data-mdurl="https://policy.example/child-mental-health" '
                'href="https://www.so.com/link?m=one">'
                "儿童心理健康服务体系建设政策"
                "</a></h3><p class=\"res-desc\">"
                "面向社会组织和学校的儿童心理健康服务项目支持政策。"
                "</p></li></ul>"
            ),
        )

    results = capture_public_web(
        "儿童心理健康 政策 资助",
        max_results=5,
        transport=httpx.MockTransport(handler),
    )

    assert [item.source_url for item in results] == [
        "https://policy.example/child-mental-health"
    ]
    assert results[0].title == "儿童心理健康服务体系建设政策"
    assert "社会组织" in results[0].summary
    assert [url.host for url in calls] == ["www.sogou.com", "www.so.com"]


def test_public_capture_can_read_candidate_body_without_storing_a_second_locator(
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        intelligence_capture_local,
        "_assert_public_network_url",
        lambda value: value,
    )

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            headers={"content-type": "text/html"},
            text=(
                "<title>儿童心理健康资助指南</title><main>"
                "2026年8月10日，某基金会发布儿童心理健康公益项目资助指南，"
                "面向教师培训与社会情感学习项目开放申报。"
                "</main>"
            ),
        )

    source = PublicCaptureItem(
        title="儿童心理健康资助指南",
        summary="公开申报信息",
        source_name="policy.example",
        source_url="https://policy.example/grants",
        captured_at="2026-08-10T00:00:00Z",
        published_at=None,
        sentiment="neutral",
        sentiment_reason="未分类",
        content_hash="a" * 64,
    )
    enriched = enrich_public_capture_item(
        source, transport=httpx.MockTransport(handler)
    )
    assert enriched.body_fetched is True
    assert "开放申报" in enriched.body_excerpt
    assert enriched.published_at == "2026-08-10"
    assert enriched.source_url == source.source_url


def test_timely_research_defaults_to_excluding_direct_project_news() -> None:
    class Runtime:
        def organization_ai_completion(self, **_kwargs: object) -> dict[str, object]:
            return {
                "content": (
                    '{"queries":["日慈基金会 儿童心理 新闻",'
                    '"儿童心理健康 政策 资助"],"includeConcepts":["儿童心理"],'
                    '"excludeConcepts":[],"coverageTarget":10}'
                )
            }

    compatibility = type("Compatibility", (), {"runtime": Runtime()})()
    context = {
        "name": "日慈基金会",
        "names": ["日慈基金会", "日慈"],
        "keywords": ["儿童心理健康"],
        "domain": "公益",
        "strategicObjective": "",
    }
    plan = _research_plan(
        compatibility,
        mode="timely",
        context=context,
        focus=[],
        excluded=[],
    )
    assert plan.direct_mention_policy == "exclude"
    assert plan.queries == ("儿童心理健康 政策 资助",)

    direct = PublicCaptureItem(
        "日慈基金会发布新项目",
        "日慈基金会近期发布儿童心理项目",
        "news.example",
        "https://news.example/rici",
        "2026-08-10T00:00:00Z",
        None,
        "neutral",
        "",
        "b" * 64,
    )
    adjacent = PublicCaptureItem(
        "儿童心理健康项目资助申报启动",
        "面向社会组织开放申报",
        "grant.example",
        "https://grant.example/open",
        "2026-08-10T00:00:00Z",
        None,
        "neutral",
        "",
        "c" * 64,
    )
    accepted, model_used, rejected = _judge_candidates(
        compatibility,
        plan=plan,
        context=context,
        items=[direct, adjacent],
        official_host="ricifoundation.com",
    )
    assert [item["sourceUrl"] for item in accepted] == [adjacent.source_url]
    assert rejected["direct_project_news"] == 1
    assert model_used is True


def test_fast_research_keeps_full_topic_and_rejects_dictionary_noise() -> None:
    compatibility = type("Compatibility", (), {"runtime": object()})()
    context = {
        "name": "日慈基金会",
        "names": ["日慈基金会", "日慈"],
        "keywords": ["儿童", "儿童心理健康"],
        "domain": "公益",
        "strategicObjective": "",
    }
    timely_plan = _research_plan(
        compatibility,
        mode="timely",
        context=context,
        focus=["和儿童心理有关的政策、资助项目"],
        excluded=[],
        use_model=False,
    )
    assert timely_plan.queries[0].startswith('"儿童心理"')
    assert all('"儿童"' not in query for query in timely_plan.queries)

    definition = PublicCaptureItem(
        "儿童从几岁到几岁称为儿童？",
        "儿童的概念与年龄定义",
        "example.org",
        "https://example.org/definition",
        "2026-08-10T00:00:00Z",
        None,
        "neutral",
        "",
        "d" * 64,
    )
    policy = PublicCaptureItem(
        "儿童心理健康服务体系建设指南",
        "有关部门发布儿童心理健康服务体系建设政策指南。",
        "policy.example",
        "https://policy.example/mental-health",
        "2026-08-10T00:00:00Z",
        None,
        "neutral",
        "",
        "e" * 64,
    )
    accepted, model_used, rejected = _judge_candidates(
        compatibility,
        plan=timely_plan,
        context=context,
        items=[definition, policy],
        official_host="ricifoundation.com",
        use_model=False,
    )
    assert [item["sourceUrl"] for item in accepted] == [policy.source_url]
    assert rejected["low_value_source"] == 1
    assert model_used is False

    brand_plan = _research_plan(
        compatibility,
        mode="brand",
        context=context,
        focus=[],
        excluded=[],
        use_model=False,
    )
    assert brand_plan.queries[0].startswith('"日慈基金会"')


def test_official_capture_crawls_only_same_origin_and_keeps_page_urls(monkeypatch) -> None:
    monkeypatch.setattr(
        intelligence_capture_local,
        "_assert_public_network_url",
        lambda value: value,
    )

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path in {"/robots.txt", "/sitemap.xml"}:
            return httpx.Response(404, headers={"content-type": "text/plain"}, text="missing")
        if request.url.path == "/":
            return httpx.Response(
                200,
                headers={"content-type": "text/html"},
                text=(
                    "<title>日慈公益基金会</title><h1>日慈公益基金会</h1>"
                    '<a href="/projects/heart">心灵魔法学院</a>'
                    '<a href="https://other.example/private">外站</a>'
                ),
            )
        assert request.url.path == "/projects/heart"
        return httpx.Response(
            200,
            headers={"content-type": "text/html"},
            text="<title>心灵魔法学院</title><main>儿童社会情感学习项目</main>",
        )

    pages = capture_official_website(
        "https://official.example/",
        transport=httpx.MockTransport(handler),
    )
    assert [item.url for item in pages] == [
        "https://official.example/",
        "https://official.example/projects/heart",
    ]
    assert pages[1].title == "心灵魔法学院"
    assert "儿童社会情感学习" in pages[1].text


def test_official_capture_uses_sitemap_when_spa_transition_page_has_no_links(monkeypatch) -> None:
    monkeypatch.setattr(
        intelligence_capture_local,
        "_assert_public_network_url",
        lambda value: value,
    )

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/robots.txt":
            return httpx.Response(
                200,
                headers={"content-type": "text/plain"},
                text="User-agent: *\nAllow: /\nSitemap: https://spa.example/sitemap.xml\n",
            )
        if request.url.path == "/sitemap.xml":
            return httpx.Response(
                200,
                headers={"content-type": "text/xml"},
                text=(
                    '<?xml version="1.0"?><urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
                    "<url><loc>https://spa.example/</loc></url>"
                    "<url><loc>https://spa.example/seo/about/</loc></url>"
                    "<url><loc>https://other.example/private</loc></url>"
                    "</urlset>"
                ),
            )
        if request.url.path == "/":
            return httpx.Response(
                200,
                headers={"content-type": "text/html"},
                text="<title>静态过渡页</title><main>正在进入网站</main>",
            )
        assert request.url.path == "/seo/about/"
        return httpx.Response(
            200,
            headers={"content-type": "text/html"},
            text="<title>关于我们</title><main>我们为公益组织提供战略陪伴与数字化支持。</main>",
        )

    pages = capture_official_website(
        "https://spa.example/",
        transport=httpx.MockTransport(handler),
    )
    # The transition shell is not a knowledge page. The sitemap still lets the
    # crawler reach the first substantive institutional entry.
    assert [page.url for page in pages] == ["https://spa.example/seo/about/"]
    assert "战略陪伴" in pages[0].text
    assert pages[0].page_role == "institutional_profile"
    assert pages[0].capture_kind == "seo_mirror"
    assert pages[0].canonical_public_url == ""


def test_rendered_dom_replaces_transition_shell_and_preserves_query_routes(monkeypatch) -> None:
    monkeypatch.setattr(
        intelligence_capture_local,
        "_assert_public_network_url",
        lambda value: value,
    )
    static = [
        intelligence_capture_local.OfficialWebsitePage(
            title="静态过渡页",
            url="https://spa.example/?page=about",
            text="正在进入网站",
            content_hash="static",
            captured_at="2026-08-10T00:00:00Z",
        )
    ]
    merged = merge_rendered_official_pages(
        "https://spa.example/?page=about",
        static,
        [
            {
                "title": "关于我们",
                "url": "https://spa.example/?page=about",
                "text": "关于我们：为公益组织提供战略陪伴与数字化支持。",
                "capturedAt": "2026-08-10T00:00:01Z",
            },
            {
                "title": "核心业务",
                "url": "https://spa.example/?page=services",
                "text": "核心业务包括战略路径、组织效能和AI落地。",
                "capturedAt": "2026-08-10T00:00:02Z",
            },
        ],
    )
    assert [page.url for page in merged] == [
        "https://spa.example/?page=about",
        "https://spa.example/?page=services",
    ]
    assert merged[0].title == "关于我们"
    assert "战略陪伴" in merged[0].text
    assert merged[0].canonical_public_url == "https://spa.example/?page=about"
    assert merged[0].page_role == "institutional_profile"


def test_rendered_core_routes_outrank_and_replace_static_seo_mirrors(monkeypatch) -> None:
    monkeypatch.setattr(
        intelligence_capture_local,
        "_assert_public_network_url",
        lambda value: value,
    )
    static = [
        intelligence_capture_local.OfficialWebsitePage(
            title="静态过渡页",
            url="https://spa.example/",
            text="正在进入网站",
            content_hash="shell",
            captured_at="2026-08-10T00:00:00Z",
        ),
        intelligence_capture_local.OfficialWebsitePage(
            title="关于我们",
            url="https://spa.example/seo/about/",
            text="简短 SEO 摘要",
            content_hash="seo-about",
            captured_at="2026-08-10T00:00:00Z",
        ),
        intelligence_capture_local.OfficialWebsitePage(
            title="一篇文章",
            url="https://spa.example/share/article/1/",
            text="文章正文足够长，可以作为补充证据，但不应排在机构入口之前。",
            content_hash="article",
            captured_at="2026-08-10T00:00:00Z",
        ),
    ]
    merged = merge_rendered_official_pages(
        "https://spa.example/",
        static,
        [
            {
                "title": "首页",
                "url": "https://spa.example/",
                "text": "这是已经加载完成的真实首页正文，包含机构定位、项目入口与主要服务。",
            },
            {
                "title": "关于我们",
                "url": "https://spa.example/?page=about",
                "text": "这是完整的机构介绍、团队、使命、愿景与发展历程。",
            },
        ],
    )
    assert [page.url for page in merged[:2]] == [
        "https://spa.example/",
        "https://spa.example/?page=about",
    ]
    assert all(page.url != "https://spa.example/seo/about/" for page in merged)
    assert merged[-1].url == "https://spa.example/share/article/1/"

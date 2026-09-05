from __future__ import annotations

import os
import threading
from datetime import datetime, timedelta

import pytest

from ashare_hotpot.config import (
    INTERACTION_SOURCES,
    RESEARCH_SOURCES,
    AppSettings,
    DEFAULT_SOURCES,
    SHANGHAI_TZ,
)
from ashare_hotpot.parsing import parse_article_detail
from ashare_hotpot.pdf import extract_pdf_text
from ashare_hotpot.industries import fetch_stock_industries
from ashare_hotpot.policy_sources import PolicySource
from ashare_hotpot.popularity import fetch_official_popularity
from ashare_hotpot.sources import (
    BseAnnouncementSource,
    BsePerformanceSource,
    CninfoSource,
    IrmIrcsSource,
    IrmSource,
    NewsSource,
    PoliteHttpClient,
    SseAnnouncementSource,
    SsePublishSource,
    SseCalendarSource,
    SseInteractionSource,
)


pytestmark = [
    pytest.mark.live,
    pytest.mark.skipif(
        os.environ.get("ASHARE_HOTPOT_LIVE_TEST") != "1",
        reason="set ASHARE_HOTPOT_LIVE_TEST=1 to access the live site",
    ),
]


def test_live_company_news_page_and_article() -> None:
    settings = AppSettings(request_retries=1, request_timeout_seconds=20)
    with PoliteHttpClient(settings, threading.Event()) as client:
        page = NewsSource(DEFAULT_SOURCES[0], client).fetch_page(1, datetime.now(SHANGHAI_TZ))
        assert 1 <= len(page.items) <= 50
        parsed = [parse_article_detail(candidate, client.get_text(candidate.url)) for candidate in page.items[:10]]
    assert any(article.stocks for article in parsed)


def test_live_industry_research_and_eastmoney_industry_mapping() -> None:
    settings = AppSettings(request_retries=1, request_timeout_seconds=30)
    industry_config = next(source for source in DEFAULT_SOURCES if source.key == "industry_research")
    now = datetime.now(SHANGHAI_TZ)
    with PoliteHttpClient(settings, threading.Event()) as client:
        page = NewsSource(industry_config, client).fetch_page(1, now)
        assert 1 <= len(page.items) <= 50
        article = parse_article_detail(page.items[0], client.get_text(page.items[0].url))
        mappings = fetch_stock_industries(client, {stock.code for stock in article.stocks})
    assert article.channel_key == "industry_research"
    assert article.title
    assert mappings or article.stocks == ()


def test_live_official_popularity_board() -> None:
    settings = AppSettings(request_retries=1, request_timeout_seconds=20)
    with PoliteHttpClient(settings, threading.Event()) as client:
        popularity, surging = fetch_official_popularity(client)
    assert 1 <= len(popularity) <= 100
    assert 1 <= len(surging) <= 100
    assert all(row.rank >= 1 for row in popularity)
    assert all(row.name and len(row.code) == 6 for row in popularity)
    assert all(row.url.startswith("https://guba.eastmoney.com/rank/stock?code=") for row in popularity)
    # Codes masquerading as names and wholly missing quote columns used to
    # pass this contract. Prices may legitimately be absent for individual
    # securities, but an all-empty quote response is never a healthy result.
    for rows in (popularity, surging):
        assert all(row.name != row.code for row in rows)
        assert any(row.current_price is not None for row in rows)
        assert any(row.change_percent is not None for row in rows)


def test_live_irm_question_stream() -> None:
    settings = AppSettings(request_retries=1, request_timeout_seconds=20)
    config = next(source for source in INTERACTION_SOURCES if source.adapter == "irm")
    with PoliteHttpClient(settings, threading.Event()) as client:
        result = IrmSource(config, client).fetch_page(1, datetime.now(SHANGHAI_TZ))
    assert len(result.items) > 0
    assert all(record.code and record.question for record in result.items)
    assert all(record.platform_name == "深交所互动易" for record in result.items)
    assert result.oldest_feed_time is not None


def test_live_sse_interaction_feed() -> None:
    settings = AppSettings(request_retries=1, request_timeout_seconds=20)
    config = next(source for source in INTERACTION_SOURCES if source.adapter == "sse")
    with PoliteHttpClient(settings, threading.Event()) as client:
        result = SseInteractionSource(config, client).fetch_page(1, datetime.now(SHANGHAI_TZ))
    assert len(result.items) > 0
    assert all(record.code and record.question for record in result.items)
    assert all(record.question_url.startswith("https://sns.sseinfo.com/qadetail.do?") for record in result.items)


@pytest.mark.skip(reason="trading-calendar synchronization retired from the active pipeline")
def test_live_sse_closed_schedule() -> None:
    settings = AppSettings(request_retries=1, request_timeout_seconds=20)
    with PoliteHttpClient(settings, threading.Event()) as client:
        year, holidays = SseCalendarSource(client).fetch_holidays()
    assert year >= 2026
    assert len(holidays) >= 10
    assert all(value.year == year for value in holidays)


@pytest.mark.skip(reason="institution research_activity synchronization retired; parser is covered offline")
def test_live_cninfo_announcement_and_research_streams() -> None:
    settings = AppSettings(request_retries=1, request_timeout_seconds=20)
    announcement_config = next(
        source for source in RESEARCH_SOURCES if source.adapter == "cninfo" and source.kind == "announcement"
    )
    research_config = next(
        source for source in RESEARCH_SOURCES if source.adapter == "cninfo" and source.kind == "research_activity"
    )
    now = datetime.now(SHANGHAI_TZ)
    with PoliteHttpClient(settings, threading.Event()) as client:
        announcement_page = CninfoSource(announcement_config, client).fetch_page(1, now)
        research_page = CninfoSource(research_config, client).fetch_page(1, now)
    assert len(announcement_page.items) > 0
    assert all(item.document_id.startswith("cninfo:") for item in announcement_page.items)
    assert all(item.title for item in announcement_page.items)
    assert any(item.stock_codes for item in announcement_page.items)
    assert len(research_page.items) > 0
    assert all(item.kind == "research_activity" for item in research_page.items)
    assert all(item.title for item in research_page.items)


@pytest.mark.skip(reason="institution research_activity synchronization retired; parser is covered offline")
def test_live_irm_ircs_investor_relation_stream() -> None:
    """互动易投资者关系活动公开流（searchTypes=4）免登录契约。

    若站点改为需要登录/出现身份页，本测试会失败并作为“必须停下并报告”的信号；
    适配器不会绕过访问控制。
    """

    settings = AppSettings(request_retries=1, request_timeout_seconds=30)
    config = next(
        source for source in RESEARCH_SOURCES if source.adapter == "irm_ircs"
    )
    now = datetime.now(SHANGHAI_TZ)
    with PoliteHttpClient(settings, threading.Event()) as client:
        result = IrmIrcsSource(config, client).fetch_page(
            1,
            now,
            date_start=now.date() - timedelta(days=7),
            date_end=now.date(),
        )
    assert len(result.items) > 0
    assert all(item.document_id.startswith("irm_ircs:") for item in result.items)
    assert all(item.kind == "research_activity" for item in result.items)
    assert all(item.stock_codes for item in result.items)
    assert all(
        item.document_url is None or item.document_url.startswith("https://static.cninfo.com.cn/")
        for item in result.items
    )
    # 分页契约：第 2 页必须返回与第 1 页不同的文档（pageNo 分页）。
    with PoliteHttpClient(settings, threading.Event()) as client:
        page_two = IrmIrcsSource(config, client).fetch_page(
            2,
            now,
            date_start=now.date() - timedelta(days=30),
            date_end=now.date(),
        )
    assert len(page_two.items) > 0
    first_ids = {item.document_id for item in result.items}
    second_ids = {item.document_id for item in page_two.items}
    assert first_ids.isdisjoint(second_ids)


@pytest.mark.skip(reason="institution research_activity synchronization retired; parser is covered offline")
def test_live_sse_publish_feed_and_real_pdf() -> None:
    settings = AppSettings(request_retries=1, request_timeout_seconds=20)
    config = next(
        source for source in RESEARCH_SOURCES if source.adapter == "sse_publish"
    )
    now = datetime.now(SHANGHAI_TZ)
    with PoliteHttpClient(settings, threading.Event()) as client:
        page = SsePublishSource(config, client).fetch_page(1, now)
        pdf_candidate = next(
            (item for item in page.items if item.attachment_type == "PDF" and item.document_url),
            None,
        )
    assert len(page.items) > 0
    assert all(item.document_id.startswith("ssepub:") for item in page.items)
    if pdf_candidate is not None:
        with PoliteHttpClient(settings, threading.Event()) as client:
            content = client.get_bytes(pdf_candidate.document_url)
        result = extract_pdf_text(content)
        assert result.error is None
        assert result.page_count is not None
        assert result.text.strip()


def test_live_sse_announcement_stream() -> None:
    """上交所公司公告 JSONP 流（queryCompanyBulletinNew.do）免登录契约。

    若站点改为需要登录、校验 Referer/指纹或接口结构变化，本测试会失败并作为
    “必须停下并报告”的信号；适配器不绕过访问控制。
    """

    settings = AppSettings(request_retries=1, request_timeout_seconds=30)
    config = next(
        source
        for source in RESEARCH_SOURCES
        if source.adapter == "sse_announcement"
    )
    now = datetime.now(SHANGHAI_TZ)
    with PoliteHttpClient(settings, threading.Event()) as client:
        result = SseAnnouncementSource(config, client).fetch_page(
            1,
            now,
            date_start=now.date() - timedelta(days=3),
            date_end=now.date(),
        )
    assert len(result.items) > 0
    assert result.total is not None and result.total > 0
    assert all(item.document_id.startswith("sse_ann:") for item in result.items)
    assert all(item.kind == "announcement" for item in result.items)
    assert all(item.stock_codes for item in result.items)
    assert all(
        item.document_url is None
        or item.document_url.startswith("https://www.sse.com.cn/")
        for item in result.items
    )
    # 分页契约：第 2 页与第 1 页文档不相交。
    with PoliteHttpClient(settings, threading.Event()) as client:
        page_two = SseAnnouncementSource(config, client).fetch_page(
            2,
            now,
            date_start=now.date() - timedelta(days=3),
            date_end=now.date(),
        )
    assert len(page_two.items) > 0
    first_ids = {item.document_id for item in result.items}
    second_ids = {item.document_id for item in page_two.items}
    assert first_ids.isdisjoint(second_ids)


@pytest.mark.skip(reason="institution research_activity synchronization retired; parser is covered offline")
def test_live_bse_performance_stream() -> None:
    """北交所业绩说明会/投资者关系活动 JSONP 流免登录契约。"""

    settings = AppSettings(request_retries=1, request_timeout_seconds=30)
    config = next(
        source
        for source in RESEARCH_SOURCES
        if source.adapter == "bse_performance"
    )
    now = datetime.now(SHANGHAI_TZ)
    with PoliteHttpClient(settings, threading.Event()) as client:
        result = BsePerformanceSource(config, client).fetch_page(
            1,
            now,
            date_start=now.date() - timedelta(days=400),
            date_end=now.date(),
        )
    assert len(result.items) > 0
    assert result.total is not None and result.total > 0
    assert all(item.document_id.startswith("bse_perf:") for item in result.items)
    assert all(item.kind == "research_activity" for item in result.items)
    assert all(item.stock_codes for item in result.items)


def test_live_bse_announcement_stream() -> None:
    """北交所官网上市公司公告 JSONP 流免登录契约。"""

    settings = AppSettings(request_retries=1, request_timeout_seconds=30)
    config = next(
        source
        for source in RESEARCH_SOURCES
        if source.adapter == "bse_announcement"
    )
    now = datetime.now(SHANGHAI_TZ)
    date_start = now.date() - timedelta(days=30)
    with PoliteHttpClient(settings, threading.Event()) as client:
        first = BseAnnouncementSource(config, client).fetch_page(
            1, now, date_start=date_start, date_end=now.date()
        )
        second = BseAnnouncementSource(config, client).fetch_page(
            2, now, date_start=date_start, date_end=now.date()
        )
    assert len(first.items) > 0
    assert len(second.items) > 0
    assert first.total is not None and first.total > 0
    assert first.total == second.total
    assert all(item.document_id.startswith("bse_ann:") for item in first.items)
    assert all(item.kind == "announcement" for item in first.items)
    assert all(item.stock_codes for item in first.items)
    assert all(
        item.document_url is None
        or item.document_url.startswith("https://www.bse.cn/disclosure/")
        for item in first.items
    )
    assert {item.document_id for item in first.items}.isdisjoint(
        {item.document_id for item in second.items}
    )


def test_live_policy_sources_list_mode() -> None:
    """服务器端渲染政策源免登录列表契约。

    部分站点会间歇性返回 JS 挑战/WAF 页（工信部 2KB 挑战页、生态环境部空响应）；
    对这些源要求“要么返回列表项，要么失败关闭”，绝不生成伪空榜。国务院/发改委/
    财政部/商务部四个稳定源必须实际返回列表项。
    """

    settings = AppSettings(request_retries=1, request_timeout_seconds=30)
    now = datetime.now(SHANGHAI_TZ)
    stable = {"state_council", "ndrc", "mof", "mofcom"}
    for key in ("state_council", "ndrc", "miit", "mof", "mofcom", "mee"):
        config = next(
            source for source in settings.policy_sources if source.key == key
        )
        try:
            with PoliteHttpClient(settings, threading.Event()) as client:
                result = PolicySource(config, client).fetch_page(1, now)
        except RuntimeError:
            # WAF/挑战页/空响应：失败关闭即契约成立（缺口在覆盖中心可见）。
            assert key not in stable, f"{key} 属于稳定源但失败关闭"
            continue
        assert len(result.items) > 0
        assert all(
            item.document_id.startswith("policy:") for item in result.items
        )
        assert all(
            item.url.startswith(("http://", "https://")) for item in result.items
        )


def test_live_policy_source_waf_fails_closed() -> None:
    """药监局 WAF（412）作为“必须停下并报告”的契约：适配器失败关闭。"""

    settings = AppSettings(request_retries=1, request_timeout_seconds=30)
    config = next(
        source for source in settings.policy_sources if source.key == "nmpa"
    )
    now = datetime.now(SHANGHAI_TZ)
    with PoliteHttpClient(settings, threading.Event()) as client:
        try:
            PolicySource(config, client).fetch_page(1, now)
        except RuntimeError:
            return  # WAF/JS 壳：失败关闭即契约成立
    raise AssertionError("药监局 WAF 应失败关闭，但适配器返回了列表")

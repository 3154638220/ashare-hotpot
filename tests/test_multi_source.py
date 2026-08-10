from __future__ import annotations

import json
import threading
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from ashare_hotpot.config import AppSettings, SHANGHAI_TZ, SourceConfig
from ashare_hotpot.dedupe import Deduplicator, dedupe_interactions, normalize_question
from ashare_hotpot.filtering import (
    interaction_noise_reason,
)
from ashare_hotpot.models import (
    ArticleCandidate,
    ParsedArticle,
    StockMention,
)
from ashare_hotpot.parsing import (
    parse_irm_page,
    parse_sse_feed,
    parse_sse_relative_time,
)
from ashare_hotpot.ranking import InteractionRankingService
from ashare_hotpot.service import RefreshService
from ashare_hotpot.sources import (
    IrmSource,
    PageResult,
    RefreshCancelled,
    SseInteractionSource,
)
from ashare_hotpot.storage import Storage


FIXTURES = Path(__file__).parent / "fixtures"
NOW = datetime(2026, 8, 5, 20, 0, tzinfo=SHANGHAI_TZ)


def _load(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


def _load_json(name: str) -> dict[str, object]:
    return json.loads(_load(name))


class StubClient:
    """Minimal PoliteHttpClient stand-in used by adapter/service tests."""

    def __init__(
        self,
        *,
        post_map: dict[tuple[str, str, str], dict[str, object]] | None = None,
        text_map: dict[str, str] | None = None,
        post_errors: dict[tuple[str, str, str], Exception] | None = None,
    ) -> None:
        self.post_map = post_map or {}
        self.text_map = text_map or {}
        self.post_errors = post_errors or {}
        self.post_calls: list[tuple[str, str, str]] = []
        self.text_calls: list[str] = []

    def __enter__(self) -> "StubClient":
        return self

    def __exit__(self, *_args: object) -> None:
        pass

    def post_form(self, url: str, payload: dict[str, object]) -> dict[str, object]:
        page = str(payload.get("pageNo") or payload.get("pageNum") or "1")
        column = str(payload.get("column") or "")
        key = (url, page, column)
        self.post_calls.append(key)
        if key in self.post_errors:
            raise self.post_errors[key]
        if key in self.post_map:
            return self.post_map[key]
        fallback = (url, page, "")
        if fallback in self.post_errors:
            raise self.post_errors[fallback]
        if fallback in self.post_map:
            return self.post_map[fallback]
        raise RuntimeError(f"未配置响应：{key}")

    def get_text(self, url: str, *, accept: str = "") -> str:
        self.text_calls.append(url)
        # First match wins; tests must order specific templates first.
        for template, value in self.text_map.items():
            if template in url:
                return value
        raise RuntimeError(f"未配置响应：{url}")

    def get_json(self, url: str) -> dict[str, object]:
        return {"result": None}


def _irm_config() -> SourceConfig:
    return SourceConfig(
        "irm",
        "深交所互动易",
        "http://irm.cninfo.com.cn/newircs/index/search",
        adapter="irm",
        provider_key="irm",
        provider_name="深交所互动易",
    )


def _sse_config() -> SourceConfig:
    return SourceConfig(
        "sse",
        "上证e互动",
        "https://sns.sseinfo.com/ajax/feeds.do",
        adapter="sse",
        provider_key="sse",
        provider_name="上证e互动",
    )


def test_parse_irm_page_records_reply_and_unanswered() -> None:
    records, total = parse_irm_page(
        _load_json("irm_page.json"),
        source_key="irm",
        source_name="深交所互动易",
        now=NOW,
    )
    assert total == 2
    answered = next(record for record in records if record.record_id == "irm:2276616300371705856")
    assert answered.code == "688567"
    assert answered.stock_name == "孚能科技"
    assert answered.industry_tags == ("制造业",)
    assert answered.replied is True
    assert answered.reply == "尊敬的投资者，感谢您的关注！"
    assert answered.reply_time is not None
    assert answered.question_url.endswith("questionId=2276616300371705856")

    unanswered = next(record for record in records if record.record_id == "irm:2276616300371700001")
    assert unanswered.replied is False
    assert unanswered.reply is None
    assert unanswered.reply_time is None
    assert unanswered.question_time == datetime.fromtimestamp(
        1785890000, tz=SHANGHAI_TZ
    )


def test_parse_sse_relative_time() -> None:
    now = datetime(2026, 8, 5, 20, 0, tzinfo=SHANGHAI_TZ)
    assert parse_sse_relative_time("刚刚", now) == now
    assert parse_sse_relative_time("5分钟前", now) == now - timedelta(minutes=5)
    assert parse_sse_relative_time("5小时前", now) == now - timedelta(hours=5)
    assert parse_sse_relative_time("3天前", now) == now - timedelta(days=3)
    assert parse_sse_relative_time("昨天 10:30", now) == datetime(
        2026, 8, 4, 10, 30, tzinfo=SHANGHAI_TZ
    )
    assert parse_sse_relative_time("2026年08月01日 09:05", now) == datetime(
        2026, 8, 1, 9, 5, tzinfo=SHANGHAI_TZ
    )
    assert parse_sse_relative_time("未知格式", now) is None


def test_parse_sse_question_feed_and_reply_feed() -> None:
    questions = parse_sse_feed(
        _load("sse_feed_questions.html"),
        source_key="sse",
        source_name="上证e互动",
        now=NOW,
    )
    assert len(questions) == 2
    by_id = {record.record_id: record for record in questions}
    first = by_id["sse:1776848"]
    assert first.code == "688629"
    assert first.stock_name == "华丰科技"
    assert first.question == "请问公司7.2T NPO光模块研发进展如何？"
    assert first.question_time == NOW - timedelta(hours=5)
    assert first.replied is False
    assert first.question_url == "https://sns.sseinfo.com/qadetail.do?weiboId=1776848"
    assert by_id["sse:1776850"].question_time == NOW

    replies = parse_sse_feed(
        _load("sse_feed_replies.html"),
        source_key="sse",
        source_name="上证e互动",
        now=NOW,
    )
    assert len(replies) == 1
    answered = replies[0]
    assert answered.code == "600645"
    assert answered.replied is True
    assert answered.reply_time == NOW - timedelta(hours=2)
    assert "国际化发展" in (answered.reply or "")


def test_parse_sse_login_page_is_empty_failure_signal() -> None:
    records = parse_sse_feed(
        _load("sse_login.html"),
        source_key="sse",
        source_name="上证e互动",
        now=NOW,
    )
    assert records == []


def test_irm_source_page_and_boundary_time() -> None:
    url = _irm_config().base_url
    client = StubClient(post_map={(url, "1", ""): _load_json("irm_page.json")})
    source = IrmSource(_irm_config(), client)
    result = source.fetch_page(1, NOW)
    assert len(result.items) == 2
    assert result.exhausted is False
    # oldest feed time is the oldest reply/question time on the page
    assert result.oldest_feed_time == datetime.fromtimestamp(1785890000, tz=SHANGHAI_TZ)


def test_sse_source_reads_latest_reply_feed_only() -> None:
    client = StubClient(
        text_map={
            "type=11": _load("sse_feed_replies.html"),
        }
    )
    source = SseInteractionSource(_sse_config(), client)
    result = source.fetch_page(1, NOW)
    assert len(result.items) == 1
    assert result.exhausted is False
    # 口径（v2）：只读最新回复流，窗口边界以回复时间为准。
    assert result.oldest_feed_time == NOW - timedelta(hours=2)
    assert len(client.text_calls) == 1
    assert "type=11" in client.text_calls[0]


def test_sse_source_login_pages_are_exhausted() -> None:
    client = StubClient(text_map={"type=": _load("sse_login.html")})
    source = SseInteractionSource(_sse_config(), client)
    result = source.fetch_page(1, NOW)
    assert result.items == ()
    assert result.exhausted is True
    assert result.oldest_feed_time is None


def test_interaction_noise_filter_is_selective() -> None:
    assert interaction_noise_reason("") == "空内容"
    assert interaction_noise_reason("你好") == "空内容"
    assert interaction_noise_reason("主力今天是不是在拉升出货？") == "纯走势/庄家/盘口"
    assert interaction_noise_reason("加群获取翻倍牛股 微信XXXX") == "垃圾内容"
    assert interaction_noise_reason("aaaaaaaaaaaaaaaaaaaa") == "无意义重复"
    # 走势类问题只要带有经营/财务/治理/重大事项语义就保留
    assert interaction_noise_reason("股价近期走势低迷，请问公司订单和产能是否正常？") is None
    assert interaction_noise_reason("公司二季度业绩如何？分红会调整吗？") is None


# ---- dedupe & ranking -----------------------------------------------------


def test_interaction_dedupe_by_id_and_duplicate_question() -> None:
    records = [
        _record("irm:1", "000001", "分红政策会调整吗？", NOW - timedelta(hours=1)),
        _record("irm:2", "000001", "分红政策会调整吗？", NOW - timedelta(hours=2)),
        _record("sse:3", "000001", "分红政策会调整吗？", NOW - timedelta(hours=30)),
        _record("sse:4", "600519", "分红政策会调整吗？", NOW - timedelta(hours=1)),
        _record("sse:5", "600519", "分红政策会调整吗？", NOW - timedelta(hours=1)),
        _record("irm:6", "000001", "未回复问题", NOW - timedelta(hours=1), replied=False),
    ]
    unique = dedupe_interactions(records)
    ids = {record.record_id for record in unique}
    # 24 小时窗口按回复时间判定：irm:1/irm:2 合并，sse:3 与 irm:2 间隔超过
    # 24 小时保留；未回复记录不参与榜单。
    assert "irm:2" in ids
    assert "irm:1" not in ids
    assert "sse:3" in ids
    assert "sse:5" not in ids
    assert "irm:6" not in ids
    assert len(unique) == 3
    assert normalize_question("分红政策会调整吗？") == "分红政策会调整吗"


def _record(
    record_id: str,
    code: str,
    question: str,
    question_time: datetime,
    *,
    replied: bool = True,
    reply_time: datetime | None = None,
):
    from ashare_hotpot.models import InteractionRecord

    return InteractionRecord(
        record_id=record_id,
        platform_key=record_id.split(":")[0],
        platform_name="测试平台",
        code=code,
        stock_name=code,
        question=question,
        question_time=question_time,
        question_url=f"https://example.test/{record_id}",
        reply="已回复" if replied else None,
        reply_time=reply_time or (question_time + timedelta(hours=1) if replied else None),
    )


def test_interaction_ranking_order_and_reply_rate() -> None:
    records = [
        _record("a:1", "600001", "问题A", NOW - timedelta(hours=3), reply_time=NOW - timedelta(hours=1)),
        _record("a:2", "600001", "问题B", NOW - timedelta(hours=4), reply_time=NOW - timedelta(hours=2)),
        _record("a:3", "600001", "问题C", NOW - timedelta(hours=5), reply_time=NOW - timedelta(hours=3)),
        _record("b:1", "000001", "问题D", NOW - timedelta(hours=2), reply_time=NOW - timedelta(minutes=30)),
        _record("b:2", "000001", "问题E", NOW - timedelta(hours=6), reply_time=NOW - timedelta(hours=4)),
    ]
    rows = InteractionRankingService().build_rankings(records)
    assert [row.code for row in rows] == ["600001", "000001"]
    assert rows[0].question_count == 3
    assert rows[0].replied_count == 3
    assert rows[0].reply_rate == pytest.approx(1.0)
    assert rows[0].latest_reply == NOW - timedelta(hours=1)
    assert rows[1].replied_count == 2
    assert rows[1].reply_rate == pytest.approx(1.0)
    assert rows[1].latest_reply == NOW - timedelta(minutes=30)


def test_reply_time_window_semantics_old_question_replied_recently() -> None:
    """回归：提问很早但刚被回复的问题按回复时间进入窗口；窗口内未回复
    的提问不计入榜单（用户反馈的中航光电 002179 场景）。"""

    old_question_replied = _record(
        "irm:old",
        "002179",
        "公司研发投入为何这么高？",
        NOW - timedelta(days=2),
        reply_time=NOW - timedelta(hours=2),
    )
    recent_unreplied = _record(
        "irm:new",
        "002179",
        "公司产能爬坡进展如何？",
        NOW - timedelta(hours=1),
        replied=False,
    )

    unique = dedupe_interactions([old_question_replied, recent_unreplied])
    assert [record.record_id for record in unique] == ["irm:old"]

    rows = InteractionRankingService().build_rankings(unique)
    assert rows[0].code == "002179"
    assert rows[0].question_count == 1
    assert rows[0].replied_count == 1
    assert rows[0].latest_reply == NOW - timedelta(hours=2)
    assert rows[0].reply_rate == pytest.approx(1.0)


def test_same_event_merges_across_channels() -> None:
    stock = StockMention("000001", "平安银行")
    company = ParsedArticle(
        "ths-1",
        "https://stock.10jqka.com.cn/20260804/c1.shtml",
        "平安银行：公布2026年半年度业绩预告",
        "",
        NOW - timedelta(hours=2),
        "companynews",
        "公司资讯",
        "同花顺财经",
        (stock,),
    )
    announcement = ParsedArticle(
        "ths-2",
        "https://stock.10jqka.com.cn/20260804/c2.shtml",
        "平安银行股份有限公司2026年半年度业绩预告",
        "",
        NOW - timedelta(hours=3),
        "announcement",
        "个股公告",
        "同花顺财经",
        (stock,),
    )
    events = Deduplicator(similarity_threshold=70).group([company, announcement])
    assert len(events) == 1
    assert len(events[0].articles) == 2

    other_stock = StockMention("600519", "贵州茅台")
    other_channel = ParsedArticle(
        "ths-3",
        "https://stock.10jqka.com.cn/20260804/c3.shtml",
        "平安银行股份有限公司2026年半年度业绩预告",
        "",
        NOW - timedelta(hours=3),
        "market_news",
        "证券市场新闻",
        "同花顺财经",
        (other_stock,),
    )
    assert len(Deduplicator(similarity_threshold=70).group([company, other_channel])) == 2

    old_article = ParsedArticle(
        "ths-4",
        "https://stock.10jqka.com.cn/20260803/c4.shtml",
        "平安银行股份有限公司2026年半年度业绩预告",
        "",
        NOW - timedelta(hours=30),
        "announcement",
        "个股公告",
        "同花顺财经",
        (stock,),
    )
    assert len(Deduplicator(similarity_threshold=70).group([company, old_article])) == 2


# ---- service pipeline -----------------------------------------------------


def _service_settings(tmp_path, *, sources, interaction_sources) -> AppSettings:
    return AppSettings(
        app_root=tmp_path,
        sources=sources,
        interaction_sources=interaction_sources,
        research_sources=(),
        policy_sources=(),
        max_pages_per_source=3,
        detail_workers=1,
    )


def _patch_dependencies(monkeypatch, client: StubClient) -> None:
    import ashare_hotpot.service as service_module

    monkeypatch.setattr(service_module, "PoliteHttpClient", lambda _settings, _cancel: client)
    monkeypatch.setattr(
        service_module,
        "fetch_official_popularity",
        lambda _client: ([], []),
    )


DETAIL_HTML = """
<html><body><span>2026-08-05 18:00:00</span>
<div class='news-content-parsed'>
  <a data-code='000001' data-type='stock'
     href='https://stockpage.10jqka.com.cn/000001'>平安银行</a>
</div></body></html>
"""


class FakeNewsSource:
    def __init__(self, config, client) -> None:
        self.config = config

    def fetch_page(self, page: int, now: datetime):
        if page == 1:
            return PageResult(
                page,
                self.config.base_url,
                (
                    ArticleCandidate(
                        "1",
                        "https://stock.10jqka.com.cn/20260804/c1.shtml",
                        "平安银行：公布2026年半年度业绩预告",
                        "",
                        NOW - timedelta(hours=2),
                        self.config.key,
                        self.config.name,
                    ),
                ),
            )
        return PageResult(page, self.config.base_url, ())


def test_refresh_pipeline_with_news_and_interactions(monkeypatch, tmp_path) -> None:
    irm_url = _irm_config().base_url
    client = StubClient(
        post_map={
            (irm_url, "1", ""): _load_json("irm_page.json"),
            (irm_url, "2", ""): _load_json("irm_empty.json"),
        },
        text_map={
            "https://stock.10jqka.com.cn/20260804/c1.shtml": DETAIL_HTML,
            "page=2": _load("sse_login.html"),
            "type=10": _load("sse_feed_questions.html"),
            "type=11": _load("sse_feed_replies.html"),
        },
    )
    _patch_dependencies(monkeypatch, client)
    import ashare_hotpot.service as service_module

    monkeypatch.setattr(service_module, "NewsSource", FakeNewsSource)
    settings = _service_settings(
        tmp_path,
        sources=(
            SourceConfig(
                "companynews",
                "公司资讯",
                "https://stock.10jqka.com.cn/companynews_list/",
            ),
        ),
        interaction_sources=(_irm_config(), _sse_config()),
    )
    storage = Storage(settings.database_path)

    snapshot = RefreshService(settings, storage).refresh(now=NOW)

    # 同花顺新闻：1 条有效新闻进入榜单，来源与类型保持默认口径。
    assert snapshot.stats["fetched"] == 1
    assert {row.code for row in snapshot.rankings} == {"000001"}
    assert snapshot.rankings[0].sources == ("同花顺",)
    assert snapshot.rankings[0].content_types == ("新闻",)

    # Interactions: 2 replied questions in window (互动易 1 条已回复 + e互动
    # 1 条已回复)；未回复的互动易问题被过滤。
    assert snapshot.stats["interaction_records"] == 2
    assert snapshot.stats["interaction_filtered"] == 0
    assert snapshot.stats["interaction_unique"] == 2
    assert len(snapshot.interaction_rankings) == 2
    by_code = {row.code: row for row in snapshot.interaction_rankings}
    assert by_code["600645"].replied_count == 1
    assert by_code["600645"].platforms == ("上证e互动",)
    assert by_code["688567"].platforms == ("深交所互动易",)
    assert by_code["688567"].question_count == 1
    assert by_code["688567"].replied_count == 1
    assert "000001" not in by_code  # 未回复提问不计入榜单
    assert len(snapshot.interaction_coverages) == 2
    assert all(coverage.reached_cutoff for coverage in snapshot.interaction_coverages)
    assert snapshot.partial is False

    # The window-aware articles were persisted for ranking/export.
    stored = storage.get_articles_between(
        NOW - timedelta(hours=24), NOW + timedelta(hours=1)
    )
    assert {
        stock.code for article in stored for stock in article.stocks
    }  # at least one stored


def test_interaction_cache_reuse_within_10_minutes(monkeypatch, tmp_path) -> None:
    irm_url = _irm_config().base_url
    client = StubClient(
        post_map={
            (irm_url, "1", ""): _load_json("irm_page.json"),
            (irm_url, "2", ""): _load_json("irm_empty.json"),
        },
        text_map={
            "page=2": _load("sse_login.html"),
            "type=10": _load("sse_feed_questions.html"),
            "type=11": _load("sse_feed_replies.html"),
        },
    )
    _patch_dependencies(monkeypatch, client)
    settings = _service_settings(
        tmp_path,
        sources=(),
        interaction_sources=(_irm_config(), _sse_config()),
    )
    service = RefreshService(settings, Storage(settings.database_path))

    first = service.refresh(now=NOW)
    calls_after_first = len(client.post_calls) + len(client.text_calls)

    second = service.refresh(now=NOW + timedelta(minutes=5))
    calls_after_second = len(client.post_calls) + len(client.text_calls)

    assert calls_after_second == calls_after_first
    assert second.stats["interaction_sources_cached"] == 2


def test_cache_is_not_reused_when_window_widens(monkeypatch, tmp_path) -> None:
    irm_url = _irm_config().base_url
    client = StubClient(
        post_map={
            (irm_url, "1", ""): _load_json("irm_page.json"),
            (irm_url, "2", ""): _load_json("irm_empty.json"),
        },
        text_map={
            "page=2": _load("sse_login.html"),
            "type=10": _load("sse_feed_questions.html"),
            "type=11": _load("sse_feed_replies.html"),
        },
    )
    _patch_dependencies(monkeypatch, client)
    narrow = _service_settings(
        tmp_path,
        sources=(),
        interaction_sources=(_irm_config(), _sse_config()),
    )
    narrow.window_hours = 2
    storage = Storage(narrow.database_path)
    RefreshService(narrow, storage).refresh(now=NOW)
    calls_after_narrow = len(client.post_calls) + len(client.text_calls)

    wide = _service_settings(
        tmp_path,
        sources=(),
        interaction_sources=(_irm_config(), _sse_config()),
    )
    wide.window_hours = 24
    RefreshService(wide, storage).refresh(now=NOW + timedelta(minutes=5))
    calls_after_wide = len(client.post_calls) + len(client.text_calls)

    # A 2h cache does not cover a 24h window: the stream must be re-read.
    assert calls_after_wide > calls_after_narrow


def test_single_interaction_source_failure_keeps_cache_and_marks_partial(
    monkeypatch, tmp_path
) -> None:
    irm_url = _irm_config().base_url
    client = StubClient(
        post_map={
            (irm_url, "1", ""): _load_json("irm_page.json"),
            (irm_url, "2", ""): _load_json("irm_empty.json"),
        },
            text_map={
                "page=2": _load("sse_login.html"),
                "type=10": _load("sse_feed_questions.html"),
                "type=11": _load("sse_feed_replies.html"),
            },
    )
    _patch_dependencies(monkeypatch, client)
    settings = _service_settings(
        tmp_path,
        sources=(),
        interaction_sources=(_irm_config(), _sse_config()),
    )
    service = RefreshService(settings, Storage(settings.database_path))

    ok = service.refresh(now=NOW)
    assert len(ok.interaction_rankings) == 2

    # 互动易 fails on the second refresh; the previous records must survive.
    client.post_errors[(irm_url, "1", "")] = RuntimeError("身份核实页")
    client.post_map.pop((irm_url, "1", ""), None)
    partial = service.refresh(now=NOW + timedelta(hours=1))

    irm_coverage = next(
        item for item in partial.interaction_coverages if item.source_key == "irm"
    )
    assert irm_coverage.error == "身份核实页"
    assert irm_coverage.reached_cutoff is False
    assert partial.partial is True
    assert len(partial.interaction_rankings) == 2  # cached 互动易 records kept
    assert partial.stats["interaction_sources_cached"] == 0


def test_all_interaction_sources_fail_without_cache_has_no_fake_board(
    monkeypatch, tmp_path
) -> None:
    irm_url = _irm_config().base_url
    client = StubClient(
        post_errors={
            (irm_url, "1", ""): RuntimeError("接口返回结构异常"),
        },
        text_map={"type=": _load("sse_login.html")},
    )
    _patch_dependencies(monkeypatch, client)
    settings = _service_settings(
        tmp_path,
        sources=(),
        interaction_sources=(_irm_config(), _sse_config()),
    )
    storage = Storage(settings.database_path)

    snapshot = RefreshService(settings, storage).refresh(now=NOW)

    assert snapshot.interaction_rankings == []
    assert snapshot.interactions == []
    assert len(snapshot.interaction_coverages) == 2
    assert all(item.error for item in snapshot.interaction_coverages)
    assert snapshot.partial is True


def test_cancelled_refresh_does_not_commit_interaction_records(
    monkeypatch, tmp_path
) -> None:
    irm_url = _irm_config().base_url
    client = StubClient(
        post_map={
            (irm_url, "1", ""): _load_json("irm_page.json"),
            (irm_url, "2", ""): _load_json("irm_empty.json"),
        },
            text_map={
                "page=2": _load("sse_login.html"),
                "type=10": _load("sse_feed_questions.html"),
                "type=11": _load("sse_feed_replies.html"),
            },
    )
    _patch_dependencies(monkeypatch, client)
    settings = _service_settings(
        tmp_path,
        sources=(),
        interaction_sources=(_irm_config(), _sse_config()),
    )
    storage = Storage(settings.database_path)
    cancel = threading.Event()
    cancel.set()

    with pytest.raises(RefreshCancelled):
        RefreshService(settings, storage).refresh(now=NOW, cancel_event=cancel)

    assert storage.load_latest_snapshot() is None
    assert storage.get_interactions_between(NOW - timedelta(hours=24), NOW) == []


# ---- storage compatibility ------------------------------------------------


def test_interaction_storage_roundtrip_and_purge(tmp_path) -> None:
    storage = Storage(tmp_path / "hotpot.db")
    old = _record("irm:old", "000001", "旧问题", NOW - timedelta(days=2))
    recent = _record("irm:new", "600519", "新问题", NOW - timedelta(hours=1))
    storage.upsert_interaction(old, NOW)
    storage.upsert_interaction(recent, NOW)

    rows = storage.get_interactions_between(NOW - timedelta(hours=24), NOW)
    assert [record.record_id for record in rows] == ["irm:new"]

    storage.purge_older_than(NOW - timedelta(days=1))
    assert [record.record_id for record in storage.get_interactions_between(NOW - timedelta(days=3), NOW)] == ["irm:new"]
    storage.purge_older_than(NOW + timedelta(days=1))
    assert storage.get_interactions_between(NOW - timedelta(days=3), NOW) == []


def test_legacy_snapshot_defaults_interaction_board_empty(tmp_path) -> None:
    storage = Storage(tmp_path / "hotpot.db")
    from ashare_hotpot.models import Snapshot

    payload = Snapshot(
        snapshot_id=None,
        window_start=NOW - timedelta(hours=24),
        window_end=NOW,
        created_at=NOW,
        partial=False,
        coverages=[],
        rankings=[],
        events=[],
        stats={"events": 0},
    ).to_dict()
    payload.pop("interactions")
    payload.pop("interaction_rankings")
    payload.pop("interaction_coverages")
    with storage._connect() as connection:
        connection.execute(
            "INSERT INTO snapshots(created_ts, window_start_ts, window_end_ts, partial, payload_json) "
            "VALUES (?, ?, ?, ?, ?)",
            (
                int(NOW.timestamp()),
                int((NOW - timedelta(hours=24)).timestamp()),
                int(NOW.timestamp()),
                0,
                json.dumps(payload, ensure_ascii=False),
            ),
        )

    loaded = storage.load_latest_snapshot()
    assert loaded is not None
    assert loaded.interactions == []
    assert loaded.interaction_rankings == []
    assert loaded.interaction_coverages == []

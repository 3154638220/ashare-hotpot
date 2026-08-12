from __future__ import annotations

import threading
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from ashare_hotpot.config import AppSettings, SHANGHAI_TZ, SourceConfig
from ashare_hotpot.models import (
    ArticleCandidate,
    ParsedArticle,
    PopularityRankRow,
    SourceDocument,
    StockMention,
)
from ashare_hotpot.service import RefreshService
from ashare_hotpot.sources import PageResult, RefreshCancelled
from ashare_hotpot.storage import Storage


NOW = datetime(2026, 8, 4, 18, 0, tzinfo=SHANGHAI_TZ)


class FakeClient:
    detail_calls = 0
    industry_calls = 0

    def __init__(self, settings, cancel_event) -> None:
        self.cancel_event = cancel_event

    def __enter__(self):
        return self

    def __exit__(self, *_args) -> None:
        pass

    def get_text(self, _url: str) -> str:
        type(self).detail_calls += 1
        return """
        <html><body><span>2026-08-04 17:00:00</span>
        <div class='news-content-parsed'>
          <a data-code='000001' data-type='stock'
             href='https://stockpage.10jqka.com.cn/000001'>平安银行</a>
        </div></body></html>
        """

    def get_json(self, _url: str) -> dict[str, object]:
        type(self).industry_calls += 1
        return {"result": {"data": [{"SECURITY_CODE": "000001", "EM2016": "金融-银行"}]}}


class FakeSource:
    def __init__(self, config, client) -> None:
        self.config = config

    def fetch_page(self, page: int, now: datetime) -> PageResult:
        if page == 1:
            items = (
                ArticleCandidate(
                    "1",
                    "https://stock.10jqka.com.cn/20260804/c1.shtml",
                    "平安银行发布新产品",
                    "",
                    NOW - timedelta(hours=1),
                    self.config.key,
                    self.config.name,
                ),
                ArticleCandidate(
                    "2",
                    "https://stock.10jqka.com.cn/20260804/c2.shtml",
                    "平安银行获融资买入1000万元",
                    "",
                    NOW - timedelta(hours=2),
                    self.config.key,
                    self.config.name,
                ),
            )
        else:
            items = (
                ArticleCandidate(
                    "old",
                    "https://stock.10jqka.com.cn/20260803/c99.shtml",
                    "旧闻",
                    "",
                    NOW - timedelta(hours=25),
                    self.config.key,
                    self.config.name,
                ),
            )
        return PageResult(page, "https://example.test", items)


def test_refresh_pipeline_and_cache(monkeypatch, tmp_path) -> None:
    import ashare_hotpot.service as service_module

    monkeypatch.setattr(service_module, "PoliteHttpClient", FakeClient)
    monkeypatch.setattr(service_module, "NewsSource", FakeSource)
    monkeypatch.setattr(service_module, "fetch_official_popularity", fake_popularity_fetch)
    FakeClient.detail_calls = 0
    FakeClient.industry_calls = 0
    settings = AppSettings(
        app_root=tmp_path,
        sources=(SourceConfig("test", "测试栏目", "https://example.test/"),),
        interaction_sources=(),
        policy_sources=(),
        research_sources=(),
        max_pages_per_source=3,
        detail_workers=1,
    )
    storage = Storage(settings.database_path)
    research_purges: list[datetime] = []
    original_research_purge = storage.purge_research_retention

    def record_research_purge(timestamp: datetime) -> None:
        research_purges.append(timestamp)
        original_research_purge(timestamp)

    monkeypatch.setattr(storage, "purge_research_retention", record_research_purge)
    progress: list[tuple[int, str]] = []
    snapshot = RefreshService(settings, storage).refresh(now=NOW, progress=lambda n, s: progress.append((n, s)))

    assert snapshot.partial is False
    assert snapshot.stats["filtered"] == 1
    assert snapshot.stats["fetched"] == 1
    assert snapshot.rankings[0].code == "000001"
    assert snapshot.rankings[0].event_count == 1
    assert snapshot.rankings[0].industry_tags == ("金融",)
    assert snapshot.stats["industry_mapped"] == 1
    assert progress[-1] == (100, "刷新完成")
    assert snapshot.popularity.available is True
    assert snapshot.popularity.popularity[0].code == "000001"
    assert snapshot.popularity.surging[0].change == 3
    assert FakeClient.detail_calls == 1
    assert FakeClient.industry_calls == 1

    second = RefreshService(settings, storage).refresh(now=NOW)
    assert second.stats["cached"] == 1
    assert second.rankings[0].industry_tags == ("金融",)
    assert FakeClient.detail_calls == 1
    assert FakeClient.industry_calls == 1
    assert research_purges == [NOW, NOW]


def test_refresh_does_not_run_retired_institution_stage(
    monkeypatch, tmp_path
) -> None:
    import ashare_hotpot.service as service_module

    monkeypatch.setattr(service_module, "PoliteHttpClient", FakeClient)
    monkeypatch.setattr(service_module, "NewsSource", FakeSource)
    monkeypatch.setattr(service_module, "fetch_official_popularity", fake_popularity_fetch)
    settings = AppSettings(
        app_root=tmp_path,
        sources=(SourceConfig("test", "测试栏目", "https://example.test/"),),
        interaction_sources=(),
        policy_sources=(),
        research_sources=(),
        max_pages_per_source=3,
        detail_workers=1,
    )
    storage = Storage(settings.database_path)
    now = datetime(2026, 8, 6, 18, 0, tzinfo=SHANGHAI_TZ)
    weekdays = [
        now.date() - timedelta(days=offset)
        for offset in range(0, 90)
        if (now.date() - timedelta(days=offset)).weekday() < 5
    ]
    storage.replace_trading_days(now.year, weekdays, source="sse", updated_at=now)
    fixture = (
        Path(__file__).parent / "fixtures" / "research_activity_record.txt"
    ).read_text(encoding="utf-8").replace(
        "2026年8月4日至8月6日", "2026年8月6日"
    )
    storage.upsert_source_document(
        SourceDocument(
            document_id="doc-wired",
            provider_key="cninfo",
            provider_name="巨潮资讯",
            kind="research_activity",
            source_url="https://example.test/list",
            document_url=None,
            title="XX科技投资者关系活动记录表",
            published_at=now,
            stock_codes=("300999",),
            body_text=fixture,
            content_hash="hash-wired",
            parse_status="parsed",
            parse_error=None,
        ),
        now,
    )

    snapshot = RefreshService(settings, storage).refresh(now=now, progress=None)

    # Legacy source documents remain readable, but refresh no longer parses
    # institution activity, syncs a calendar, or publishes institution metrics.
    assert snapshot.stats["research_calendar_days"] == 0
    assert storage.get_latest_institution_metric_snapshots("z20") == {}


def test_refresh_runs_policy_sync_and_writes_stats(monkeypatch, tmp_path) -> None:
    """v2 里程碑 2：政策观察来源接入 RefreshService——可解析源入政策文档，
    失败关闭源记录失败并进入快照覆盖；统计与质量文本可见。"""

    import ashare_hotpot.service as service_module

    fixtures = Path(__file__).parent / "fixtures"
    state_council_html = (
        fixtures / "policy_state_council_page1.html"
    ).read_text(encoding="utf-8")
    nmpa_waf_html = (fixtures / "policy_nmpa_page1.html").read_text(
        encoding="utf-8"
    )

    class PolicyRefreshClient(FakeClient):
        def __init__(self, settings, cancel_event) -> None:
            super().__init__(settings, cancel_event)
            all_policy = AppSettings().policy_sources
            self.policy_prefixes = {
                next(
                    s for s in all_policy if s.key == "state_council"
                ).list_url: state_council_html,
                next(s for s in all_policy if s.key == "nmpa").list_url: (
                    nmpa_waf_html
                ),
            }

        def get_text(self, url: str, *, accept: str = "", headers=None) -> str:
            for prefix, text in self.policy_prefixes.items():
                if url.startswith(prefix):
                    return text
            return super().get_text(url)

    monkeypatch.setattr(service_module, "PoliteHttpClient", PolicyRefreshClient)
    monkeypatch.setattr(service_module, "NewsSource", FakeSource)
    monkeypatch.setattr(
        service_module, "fetch_official_popularity", fake_popularity_fetch
    )
    all_policy = AppSettings().policy_sources
    policy_configs = (
        next(s for s in all_policy if s.key == "state_council"),
        next(s for s in all_policy if s.key == "nmpa"),
    )
    settings = AppSettings(
        app_root=tmp_path,
        sources=(SourceConfig("test", "测试栏目", "https://example.test/"),),
        interaction_sources=(),
        research_sources=(),
        policy_sources=policy_configs,
        max_pages_per_source=3,
        detail_workers=1,
    )
    storage = Storage(settings.database_path)

    snapshot = RefreshService(settings, storage).refresh(now=NOW, progress=None)

    assert snapshot.stats["policy_sources_total"] == 2
    assert snapshot.stats["policy_documents_added"] >= 5  # 国务院列表页
    assert snapshot.stats["policy_failure_sources"] == 1  # 药监局 WAF 失败关闭
    assert len(snapshot.policy_coverages) == 2
    by_key = {
        cov.source_key: cov for cov in snapshot.policy_coverages
    }
    assert by_key["state_council"].error is None
    assert by_key["nmpa"].error is not None
    # 政策文档绝不进入信号管线（policy_documents 独立存储）。
    assert len(storage.get_policy_documents()) >= 5
    # 质量文本展示政策源状态。
    from ashare_hotpot.research_views import build_discovery_quality

    quality = build_discovery_quality(settings, storage)
    assert "政策源：" in quality
    assert "国务院" in quality


def test_refresh_ignores_retired_activity_sources_and_calendar(
    monkeypatch, tmp_path
) -> None:
    import ashare_hotpot.service as service_module

    monkeypatch.setattr(service_module, "PoliteHttpClient", FakeClient)
    monkeypatch.setattr(
        service_module, "fetch_official_popularity", fake_popularity_fetch
    )
    metric_now = datetime(2026, 8, 6, 18, 0, tzinfo=SHANGHAI_TZ)
    settings = AppSettings(
        app_root=tmp_path,
        sources=(),
        interaction_sources=(),
        policy_sources=(),
        research_sources=(
            SourceConfig(
                "test_research",
                "测试调研",
                "https://example.test/research",
                kind="research_activity",
            ),
        ),
        detail_workers=1,
    )
    storage = Storage(settings.database_path)
    fixture = (
        Path(__file__).parent / "fixtures" / "research_activity_record.txt"
    ).read_text(encoding="utf-8").replace(
        "2026年8月4日至8月6日", "2026年8月6日"
    )
    storage.upsert_source_document(
        SourceDocument(
            document_id="doc-calendar-wired",
            provider_key="cninfo",
            provider_name="巨潮资讯",
            kind="research_activity",
            source_url="https://example.test/list",
            document_url=None,
            title="XX科技投资者关系活动记录表",
            published_at=metric_now,
            stock_codes=("300999",),
            body_text=fixture,
            content_hash="hash-calendar-wired",
            parse_status="parsed",
            parse_error=None,
        ),
        metric_now,
    )

    snapshot = RefreshService(settings, storage).refresh(now=metric_now)

    assert snapshot.stats["research_calendar_days"] == 0
    assert storage.get_latest_institution_metric_snapshots("z20") == {}
    assert storage.get_trading_day_source(NOW.year) is None


def test_refresh_uses_configured_window_hours(monkeypatch, tmp_path) -> None:
    import ashare_hotpot.service as service_module

    monkeypatch.setattr(service_module, "PoliteHttpClient", FakeClient)
    monkeypatch.setattr(service_module, "NewsSource", FakeSource)
    monkeypatch.setattr(service_module, "fetch_official_popularity", fake_popularity_fetch)
    settings = AppSettings(
        app_root=tmp_path,
        sources=(SourceConfig("test", "测试栏目", "https://example.test/"),),
        interaction_sources=(),
        policy_sources=(),
        research_sources=(),
        window_hours=2,
        max_pages_per_source=3,
        detail_workers=1,
    )
    storage = Storage(settings.database_path)

    snapshot = RefreshService(settings, storage).refresh(now=NOW)

    assert snapshot.window_start == NOW - timedelta(hours=2)
    assert snapshot.window_end == NOW
    assert snapshot.stats["list_items"] == 2


def test_industry_research_keeps_24h_window_when_news_window_is_12h(
    monkeypatch, tmp_path
) -> None:
    import ashare_hotpot.service as service_module

    monkeypatch.setattr(service_module, "PoliteHttpClient", FakeClient)
    monkeypatch.setattr(service_module, "NewsSource", FakeSource)
    monkeypatch.setattr(
        service_module, "fetch_official_popularity", fake_popularity_fetch
    )
    settings = AppSettings(
        app_root=tmp_path,
        sources=(
            SourceConfig(
                "industry_research",
                "行业研究",
                "https://example.test/industry",
            ),
        ),
        interaction_sources=(),
        policy_sources=(),
        research_sources=(),
        window_hours=12,
        max_pages_per_source=3,
        detail_workers=1,
    )
    storage = Storage(settings.database_path)

    snapshot = RefreshService(settings, storage).refresh(now=NOW)

    # The -25h row is the cutoff probe; both -1h and -2h are retained for B,
    # even though the ordinary snapshot window is only 12 hours.
    assert snapshot.window_start == NOW - timedelta(hours=12)
    assert snapshot.stats["list_items"] == 2
    assert snapshot.industry_heat.window_start == NOW - timedelta(hours=24)
    assert snapshot.industry_heat.research_article_total == 2
    assert snapshot.industry_heat.rows[0].industry == "金融"
    assert snapshot.industry_heat.rows[0].b == 2
    assert snapshot.industry_heat.source_status == "complete"
    assert snapshot.stats["industry_heat_source_complete"] == 1

    # The second refresh uses the article and industry caches instead of
    # fetching details or the public industry endpoint again.
    detail_calls = FakeClient.detail_calls
    industry_calls = FakeClient.industry_calls
    second = RefreshService(settings, storage).refresh(now=NOW)
    assert second.industry_heat.rows[0].b == 2
    assert FakeClient.detail_calls == detail_calls
    assert FakeClient.industry_calls == industry_calls


def test_industry_daily_snapshot_publishes_after_18_only_once(
    monkeypatch, tmp_path
) -> None:
    import ashare_hotpot.service as service_module

    monkeypatch.setattr(service_module, "PoliteHttpClient", FakeClient)
    monkeypatch.setattr(service_module, "NewsSource", FakeSource)
    monkeypatch.setattr(service_module, "fetch_official_popularity", fake_popularity_fetch)
    settings = AppSettings(
        app_root=tmp_path,
        sources=(SourceConfig("industry_research", "行业研究", "https://example.test/industry"),),
        interaction_sources=(),
        policy_sources=(),
        research_sources=(),
        window_hours=12,
        max_pages_per_source=3,
        detail_workers=1,
    )
    storage = Storage(settings.database_path)
    service = RefreshService(settings, storage)

    before = service.refresh(now=datetime(2026, 8, 4, 17, 0, tzinfo=SHANGHAI_TZ))
    assert before.stats["industry_heat_daily_published"] == 0
    assert storage.get_industry_daily_snapshots() == []

    first = service.refresh(now=datetime(2026, 8, 4, 18, 0, tzinfo=SHANGHAI_TZ))
    assert first.stats["industry_heat_daily_published"] == 1
    assert len(storage.get_industry_daily_snapshots()) == 1

    second = service.refresh(now=datetime(2026, 8, 4, 18, 5, tzinfo=SHANGHAI_TZ))
    assert second.stats["industry_heat_daily_published"] == 0
    assert len(storage.get_industry_daily_snapshots()) == 1


def test_industry_article_failure_blocks_history_and_retries(
    monkeypatch, tmp_path
) -> None:
    import ashare_hotpot.service as service_module

    class RetryClient(FakeClient):
        failures_remaining = 1

        def get_text(self, url: str) -> str:
            if "c1.shtml" in url and type(self).failures_remaining:
                type(self).failures_remaining -= 1
                raise RuntimeError("detail timeout")
            return super().get_text(url)

    monkeypatch.setattr(service_module, "PoliteHttpClient", RetryClient)
    monkeypatch.setattr(service_module, "NewsSource", FakeSource)
    monkeypatch.setattr(service_module, "fetch_official_popularity", fake_popularity_fetch)
    settings = AppSettings(
        app_root=tmp_path,
        sources=(SourceConfig("industry_research", "行业研究", "https://example.test/industry"),),
        interaction_sources=(),
        policy_sources=(),
        research_sources=(),
        window_hours=12,
        max_pages_per_source=3,
        detail_workers=1,
    )
    storage = Storage(settings.database_path)
    service = RefreshService(settings, storage)

    failed = service.refresh(now=NOW)
    assert failed.stats["industry_heat_article_failures"] == 1
    assert failed.industry_heat.source_status == "failed"
    assert storage.get_industry_daily_snapshots() == []

    recovered = service.refresh(now=NOW + timedelta(minutes=11))
    assert recovered.stats["industry_heat_article_failures"] == 0
    assert recovered.industry_heat.source_status == "complete"
    assert recovered.stats["industry_heat_daily_published"] == 1
    assert len(storage.get_industry_daily_snapshots()) == 1


def test_cancelled_refresh_does_not_create_snapshot(monkeypatch, tmp_path) -> None:
    import ashare_hotpot.service as service_module

    monkeypatch.setattr(service_module, "PoliteHttpClient", FakeClient)
    settings = AppSettings(
        app_root=tmp_path,
        sources=(SourceConfig("test", "测试栏目", "https://example.test/"),),
        interaction_sources=(),
        policy_sources=(),
        research_sources=(),
    )
    storage = Storage(settings.database_path)
    cancel = threading.Event()
    cancel.set()
    with pytest.raises(RefreshCancelled):
        RefreshService(settings, storage).refresh(now=NOW, cancel_event=cancel)
    assert storage.load_latest_snapshot() is None


def test_refresh_reapplies_brokerage_filter_to_cached_articles(monkeypatch, tmp_path) -> None:
    import ashare_hotpot.service as service_module

    rating_url = "https://stock.10jqka.com.cn/20260804/crating.shtml"

    class CachedRatingSource:
        def __init__(self, config, client) -> None:
            self.config = config

        def fetch_page(self, page: int, now: datetime) -> PageResult:
            if page == 1:
                return PageResult(
                    page,
                    "https://example.test",
                    (
                        ArticleCandidate(
                            "rating",
                            rating_url,
                            "中信证券：维持贵州茅台买入评级",
                            "",
                            NOW - timedelta(hours=1),
                            self.config.key,
                            self.config.name,
                        ),
                    ),
                )
            return PageResult(page, "https://example.test", ())

    monkeypatch.setattr(service_module, "PoliteHttpClient", FakeClient)
    monkeypatch.setattr(service_module, "NewsSource", CachedRatingSource)
    monkeypatch.setattr(service_module, "fetch_official_popularity", fake_popularity_fetch)
    settings = AppSettings(
        app_root=tmp_path,
        sources=(SourceConfig("test", "测试栏目", "https://example.test/"),),
        interaction_sources=(),
        policy_sources=(),
        research_sources=(),
        max_pages_per_source=2,
        detail_workers=1,
    )
    storage = Storage(settings.database_path)
    storage.upsert_article(
        ParsedArticle(
            "rating",
            rating_url,
            "中信证券：维持贵州茅台买入评级",
            "",
            NOW - timedelta(hours=1),
            "test",
            "测试栏目",
            "测试来源",
            (StockMention("600030", "中信证券"), StockMention("600519", "贵州茅台")),
        ),
        NOW,
    )

    snapshot = RefreshService(settings, storage).refresh(now=NOW)

    assert snapshot.stats["cached"] == 1
    assert [row.code for row in snapshot.rankings] == ["600519"]


def test_popularity_10min_cache_only_fetches_once(monkeypatch, tmp_path) -> None:
    import ashare_hotpot.service as service_module

    calls: list[int] = []

    def counting_fetch(_client):
        calls.append(1)
        return fake_popularity_fetch(_client)

    monkeypatch.setattr(service_module, "PoliteHttpClient", FakeClient)
    monkeypatch.setattr(service_module, "NewsSource", FakeSource)
    monkeypatch.setattr(service_module, "fetch_official_popularity", counting_fetch)
    settings = AppSettings(
        app_root=tmp_path,
        sources=(SourceConfig("test", "测试栏目", "https://example.test/"),),
        interaction_sources=(),
        policy_sources=(),
        research_sources=(),
        max_pages_per_source=2,
        detail_workers=1,
    )
    service = RefreshService(settings, Storage(settings.database_path))

    first = service.refresh(now=NOW)
    assert len(calls) == 1
    assert first.popularity.success_at == NOW

    second = service.refresh(now=NOW + timedelta(minutes=5))
    assert len(calls) == 1
    assert second.popularity.success_at == NOW

    third = service.refresh(now=NOW + timedelta(minutes=11))
    assert len(calls) == 2
    assert third.popularity.success_at == NOW + timedelta(minutes=11)


@pytest.mark.parametrize(
    "failure_message",
    ["身份核实页", "HTTP 500 服务器错误", "东方财富人气榜返回空数据", "东方财富人气榜接口返回结构异常"],
)
def test_popularity_failure_falls_back_to_last_success(monkeypatch, tmp_path, failure_message) -> None:
    import ashare_hotpot.service as service_module

    state = {"fail": False}

    def flaky_fetch(_client):
        if state["fail"]:
            raise RuntimeError(failure_message)
        return fake_popularity_fetch(_client)

    monkeypatch.setattr(service_module, "PoliteHttpClient", FakeClient)
    monkeypatch.setattr(service_module, "NewsSource", FakeSource)
    monkeypatch.setattr(service_module, "fetch_official_popularity", flaky_fetch)
    settings = AppSettings(
        app_root=tmp_path,
        sources=(SourceConfig("test", "测试栏目", "https://example.test/"),),
        interaction_sources=(),
        policy_sources=(),
        research_sources=(),
        max_pages_per_source=2,
        detail_workers=1,
    )
    service = RefreshService(settings, Storage(settings.database_path))

    ok = service._refresh_popularity(now=NOW, cancel=threading.Event(), progress=None)
    assert ok.available is True
    assert ok.is_stale is False
    assert ok.success_at == NOW

    state["fail"] = True
    failed = service._refresh_popularity(
        now=NOW + timedelta(minutes=11),
        cancel=threading.Event(),
        progress=None,
    )
    assert failed.available is True
    assert failed.is_stale is True
    assert failed.success_at == NOW
    assert failure_message in failed.error
    assert failed.popularity[0].code == "000001"


def test_popularity_failure_without_history_is_unavailable(monkeypatch, tmp_path) -> None:
    import ashare_hotpot.service as service_module

    def failing_fetch(_client):
        raise RuntimeError("空数据")

    monkeypatch.setattr(service_module, "PoliteHttpClient", FakeClient)
    monkeypatch.setattr(service_module, "NewsSource", FakeSource)
    monkeypatch.setattr(service_module, "fetch_official_popularity", failing_fetch)
    settings = AppSettings(
        app_root=tmp_path,
        sources=(SourceConfig("test", "测试栏目", "https://example.test/"),),
        interaction_sources=(),
        policy_sources=(),
        research_sources=(),
        max_pages_per_source=2,
        detail_workers=1,
    )
    service = RefreshService(settings, Storage(settings.database_path))

    result = service._refresh_popularity(now=NOW, cancel=threading.Event(), progress=None)
    assert result.available is False
    assert result.is_stale is False
    assert result.success_at is None
    assert result.popularity == []
    assert result.surging == []
    assert "空数据" in result.error


def fake_popularity_fetch(_client):
    return (
        [
            PopularityRankRow(
                1,
                "000001",
                "平安银行",
                None,
                11.25,
                1.5,
                "https://guba.eastmoney.com/rank/stock?code=000001",
            )
        ],
        [
            PopularityRankRow(
                2,
                "600519",
                "贵州茅台",
                3,
                1600.0,
                2.0,
                "https://guba.eastmoney.com/rank/stock?code=600519",
            )
        ],
    )

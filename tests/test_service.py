from __future__ import annotations

import threading
from datetime import datetime, timedelta

import pytest

from ashare_hotpot.config import AppSettings, SHANGHAI_TZ, SourceConfig
from ashare_hotpot.models import ArticleCandidate, ParsedArticle, PopularityRankRow, StockMention
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
        max_pages_per_source=3,
        detail_workers=1,
    )
    storage = Storage(settings.database_path)
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


def test_refresh_uses_configured_window_hours(monkeypatch, tmp_path) -> None:
    import ashare_hotpot.service as service_module

    monkeypatch.setattr(service_module, "PoliteHttpClient", FakeClient)
    monkeypatch.setattr(service_module, "NewsSource", FakeSource)
    monkeypatch.setattr(service_module, "fetch_official_popularity", fake_popularity_fetch)
    settings = AppSettings(
        app_root=tmp_path,
        sources=(SourceConfig("test", "测试栏目", "https://example.test/"),),
        window_hours=2,
        max_pages_per_source=3,
        detail_workers=1,
    )
    storage = Storage(settings.database_path)

    snapshot = RefreshService(settings, storage).refresh(now=NOW)

    assert snapshot.window_start == NOW - timedelta(hours=2)
    assert snapshot.window_end == NOW
    assert snapshot.stats["list_items"] == 2


def test_cancelled_refresh_does_not_create_snapshot(monkeypatch, tmp_path) -> None:
    import ashare_hotpot.service as service_module

    monkeypatch.setattr(service_module, "PoliteHttpClient", FakeClient)
    settings = AppSettings(
        app_root=tmp_path,
        sources=(SourceConfig("test", "测试栏目", "https://example.test/"),),
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

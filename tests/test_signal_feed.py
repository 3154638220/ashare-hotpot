from __future__ import annotations

import threading
from datetime import datetime, timedelta
from pathlib import Path

from ashare_hotpot.config import (
    AppSettings,
    DEFAULT_SOURCES,
    SHANGHAI_TZ,
    SourceConfig,
)
from ashare_hotpot.models import ArticleCandidate
from ashare_hotpot.parsing import (
    extract_body_text,
    parse_article_detail,
    parse_list_page,
)
from ashare_hotpot.service import RefreshService
from ashare_hotpot.sources import PageResult
from ashare_hotpot.storage import Storage


FIXTURES = Path(__file__).parent / "fixtures"
NOW = datetime(2026, 8, 8, 23, 0, tzinfo=SHANGHAI_TZ)


def _interaction_config() -> SourceConfig:
    return next(
        source for source in DEFAULT_SOURCES if source.key == "company_interaction"
    )


def _candidate(
    url: str = "https://yuanchuang.10jqka.com.cn/20260807/c678772618.shtml",
) -> ArticleCandidate:
    return ArticleCandidate(
        seq=url.rsplit("/", 1)[-1].removeprefix("c").removesuffix(".shtml"),
        url=url,
        title="长芯博创：公司于2026年7月27日披露子公司长芯盛与某现有客户签署约45亿元光纤光缆长期协议，有助于深化与客户的合作关系",
        summary="",
        published_at=NOW - timedelta(hours=2),
        channel_key="company_interaction",
        channel_name="独家公司互动",
    )


def _load(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# fixture parse contracts (real 同花顺 company-interaction pages)
# ---------------------------------------------------------------------------


def test_djgshd_list_fixture_contract() -> None:
    config = _interaction_config()
    assert config.signal_feed is True
    items = parse_list_page(
        _load("ths_djgshd_list_page.html"),
        source_key=config.key,
        source_name=config.name,
        base_url=config.base_url,
        now=NOW,
    )
    assert len(items) == 4
    first = items[0]
    assert first.seq == "678790293"
    assert first.title.startswith("匠心家居")
    assert first.url == "https://yuanchuang.10jqka.com.cn/20260808/c678790293.shtml"
    assert first.published_at == datetime(2026, 8, 8, 21, 55, tzinfo=SHANGHAI_TZ)
    assert all(item.channel_key == "company_interaction" for item in items)


def test_djgshd_article_fixture_extracts_stock_and_body() -> None:
    html = _load("ths_djgshd_article_changxin.html")
    article = parse_article_detail(_candidate(), html)
    # The 同花顺（300033）newsroom byline must not count as a stock mention.
    assert [(stock.code, stock.name) for stock in article.stocks] == [
        ("300548", "长芯博创")
    ]
    body = extract_body_text(html)
    assert "45亿元" in body
    assert "预计不会对业绩构成重大影响" in body


def test_djgshd_article_fixture_neutral_reply_keeps_body() -> None:
    html = _load("ths_djgshd_article.html")
    article = parse_article_detail(
        _candidate(
            "https://yuanchuang.10jqka.com.cn/20260808/c678790293.shtml"
        ),
        html,
    )
    assert [(stock.code, stock.name) for stock in article.stocks] == [
        ("301061", "匠心家居")
    ]
    body = extract_body_text(html)
    assert "公司回答表示" in body
    assert "动态调整" in body


# ---------------------------------------------------------------------------
# refresh pipeline: signal-feed articles enter source_documents and the
# short-term signal pipeline
# ---------------------------------------------------------------------------


class StubClient:
    """Minimal PoliteHttpClient stand-in for service tests (no network)."""

    def __init__(self, text_map: dict[str, str]) -> None:
        self.text_map = text_map
        self.text_calls: list[str] = []

    def __enter__(self) -> "StubClient":
        return self

    def __exit__(self, *_args: object) -> None:
        pass

    def get_text(self, url: str, *, accept: str = "") -> str:
        self.text_calls.append(url)
        for template, value in self.text_map.items():
            if template in url:
                return value
        raise RuntimeError(f"unmapped url: {url}")

    def get_json(self, url: str) -> dict[str, object]:
        return {"result": None}


def _patch_dependencies(monkeypatch, client: StubClient) -> None:
    import ashare_hotpot.service as service_module

    monkeypatch.setattr(
        service_module, "PoliteHttpClient", lambda _settings, _cancel: client
    )
    monkeypatch.setattr(
        service_module,
        "fetch_official_popularity",
        lambda _client: ([], []),
    )


class FakeSignalFeedSource:
    def __init__(self, config: SourceConfig, client: StubClient) -> None:
        self.config = config
        self.client = client

    def fetch_page(self, page: int, now: datetime) -> PageResult:
        if page == 1:
            return PageResult(
                page,
                self.config.base_url,
                (_candidate(),),
            )
        return PageResult(page, self.config.base_url, ())


def _settings(tmp_path) -> AppSettings:
    return AppSettings(
        app_root=tmp_path,
        sources=(_interaction_config(),),
        interaction_sources=(),
        research_sources=(),
        max_pages_per_source=1,
        detail_workers=1,
    )


def test_signal_feed_article_enters_signal_pipeline(monkeypatch, tmp_path) -> None:
    client = StubClient(
        {
            "c678772618.shtml": _load("ths_djgshd_article_changxin.html"),
        }
    )
    _patch_dependencies(monkeypatch, client)
    import ashare_hotpot.service as service_module

    monkeypatch.setattr(service_module, "NewsSource", FakeSignalFeedSource)
    settings = _settings(tmp_path)
    storage = Storage(settings.database_path)

    snapshot = RefreshService(settings, storage).refresh(now=NOW)

    # The article stays a normal news-board article...
    assert snapshot.stats["fetched"] == 1
    assert {row.code for row in snapshot.rankings} == {"300548"}
    # ...and is persisted as a news SourceDocument for the signal pipeline.
    documents = storage.get_source_documents_between(
        NOW - timedelta(hours=48), NOW + timedelta(hours=1)
    )
    assert len(documents) == 1
    document = documents[0]
    assert document.document_id == (
        "ths_news:https://yuanchuang.10jqka.com.cn/20260807/c678772618.shtml"
    )
    assert document.kind == "news"
    assert document.provider_key == "ths"
    assert document.parse_status == "parsed"
    assert document.stock_codes == ("300548",)
    assert document.stock_names == {"300548": "长芯博创"}
    assert "45亿元" in document.body_text

    # The short-term signal pipeline consumed the document: the real reply
    # wording ("签署约45亿元光纤光缆长期协议") is not one of the ten fixed
    # rule patterns yet, so the conservative pipeline persists an extraction
    # with no_valid_signal instead of fabricating a positive signal.
    assert snapshot.stats["signal_documents"] >= 1
    assert snapshot.stats["signal_clusters_created"] >= 1
    assert snapshot.stats["signal_extractions"] >= 1
    clusters = storage.get_event_clusters_active(
        NOW - timedelta(hours=48), NOW + timedelta(hours=1)
    )
    assert len(clusters) == 1
    extraction = storage.get_event_extraction(clusters[0].event_id, "300548")
    assert extraction is not None
    assert extraction.extractor_kind == "rules"
    assert extraction.event_type == "unsupported_event_type"
    assert extraction.no_valid_signal is True
    assert storage.get_event_signals() == []

    # A second refresh is idempotent: same document_id, no duplicate rows.
    RefreshService(settings, storage).refresh(now=NOW + timedelta(minutes=5))
    documents = storage.get_source_documents_between(
        NOW - timedelta(hours=48), NOW + timedelta(hours=2)
    )
    assert len(documents) == 1


def test_signal_feed_article_with_major_contract_produces_catalyst(
    monkeypatch, tmp_path
) -> None:
    """A company reply carrying an explicit signed contract reaches the
    potential-catalyst board with the 0.60 media-confidence tier."""

    html = """
    <html><body><div class="news-content-parsed">
      <p><a class="link-wrapper link-blue"
        href="https://stockpage.10jqka.com.cn/300548">长芯博创（300548）</a>
        提问，公司最近经营情况如何？</p>
      <p>公司回答表示，公司近日与客户签订重大合同，合同金额1.2亿元，
        占公司最近一个会计年度营业收入的20%，合同已签署生效。</p>
    </div></body></html>
    """

    class FakeContractSource(FakeSignalFeedSource):
        def fetch_page(self, page: int, now: datetime) -> PageResult:
            if page == 1:
                return PageResult(
                    page,
                    self.config.base_url,
                    (
                        ArticleCandidate(
                            seq="678000001",
                            url="https://yuanchuang.10jqka.com.cn/20260808/c678000001.shtml",
                            title="长芯博创：公司与客户签订重大合同，合同金额1.2亿元",
                            summary="",
                            published_at=NOW - timedelta(hours=2),
                            channel_key="company_interaction",
                            channel_name="独家公司互动",
                        ),
                    ),
                )
            return PageResult(page, self.config.base_url, ())

    client = StubClient({"c678000001.shtml": html})
    _patch_dependencies(monkeypatch, client)
    import ashare_hotpot.service as service_module

    monkeypatch.setattr(service_module, "NewsSource", FakeContractSource)
    settings = _settings(tmp_path)
    storage = Storage(settings.database_path)

    snapshot = RefreshService(settings, storage).refresh(now=NOW)

    assert snapshot.stats["signal_clusters_created"] == 1
    assert snapshot.stats["signal_extractions"] == 1
    assert snapshot.stats["signal_catalyst"] == 1
    assert snapshot.stats["signal_confirmed"] == 0
    signals = storage.get_event_signals("potential_catalyst")
    assert len(signals) == 1
    signal = signals[0]
    assert signal.stock_code == "300548"
    assert signal.source_confidence == 0.6  # media tier, not official disclosure
    assert signal.provisional is True
    extraction = storage.get_event_extraction(signal.event_id, "300548")
    assert extraction is not None
    assert extraction.event_type == "major_contract"
    assert extraction.no_valid_signal is False
    assert extraction.positive_mechanism is not None
    assert any(metric["name"] == "合同金额" for metric in extraction.metrics)


def test_regular_news_source_does_not_write_signal_documents(
    monkeypatch, tmp_path
) -> None:
    """Non-signal-feed news sources keep their old articles-table behaviour."""

    detail_html = """
    <html><body><div class="news-content-parsed">
      <a data-code='000001' data-type='stock'
         href='https://stockpage.10jqka.com.cn/000001'>平安银行</a>
      <p>公司公告正文。</p>
    </div></body></html>
    """

    class FakeNewsSource:
        def __init__(self, config: SourceConfig, client: StubClient) -> None:
            self.config = config

        def fetch_page(self, page: int, now: datetime) -> PageResult:
            if page == 1:
                return PageResult(
                    page,
                    self.config.base_url,
                    (
                        ArticleCandidate(
                            seq="1",
                            url="https://stock.10jqka.com.cn/20260808/c1.shtml",
                            title="平安银行：公司2026年半年度业绩预告",
                            summary="",
                            published_at=NOW - timedelta(hours=2),
                            channel_key="companynews",
                            channel_name="公司资讯",
                        ),
                    ),
                )
            return PageResult(page, self.config.base_url, ())

    config = SourceConfig(
        "companynews",
        "公司资讯",
        "https://stock.10jqka.com.cn/companynews_list/",
    )
    assert config.signal_feed is False
    client = StubClient({"c1.shtml": detail_html})
    _patch_dependencies(monkeypatch, client)
    import ashare_hotpot.service as service_module

    monkeypatch.setattr(service_module, "NewsSource", FakeNewsSource)
    settings = AppSettings(
        app_root=tmp_path,
        sources=(config,),
        interaction_sources=(),
        research_sources=(),
        max_pages_per_source=1,
        detail_workers=1,
    )
    storage = Storage(settings.database_path)

    snapshot = RefreshService(settings, storage).refresh(now=NOW)

    assert snapshot.stats["fetched"] == 1
    assert {row.code for row in snapshot.rankings} == {"000001"}
    assert storage.get_source_documents_between(
        NOW - timedelta(hours=48), NOW + timedelta(hours=1)
    ) == []
    assert snapshot.stats["signal_documents"] == 0

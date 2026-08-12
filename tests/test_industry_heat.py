from __future__ import annotations

from datetime import datetime, timedelta

from ashare_hotpot.config import SHANGHAI_TZ
from ashare_hotpot.industry_heat import build_industry_heat_snapshot, map_industry_alias
from ashare_hotpot.models import ParsedArticle, PopularityRankRow, Snapshot, StockMention


NOW = datetime(2026, 8, 12, 18, 0, tzinfo=SHANGHAI_TZ)


def _pop(code: str, rank: int) -> PopularityRankRow:
    return PopularityRankRow(rank, code, code, None, None, None, f"https://e.test/{code}")


def _article(
    seq: str,
    published_at: datetime,
    *,
    tags: tuple[str, ...] = (),
    stocks: tuple[StockMention, ...] = (),
    url: str | None = None,
) -> ParsedArticle:
    return ParsedArticle(
        seq=seq,
        url=url or f"https://news.test/{seq}",
        title=seq,
        summary="正文",
        published_at=published_at,
        channel_key="industry_research",
        channel_name="行业研究",
        source_name="同花顺",
        stocks=stocks,
        industry_tags=tags,
    )


def test_industry_aliases_are_fixed_and_conservative() -> None:
    assert map_industry_alias(" 化工 ") == "基础化工"
    assert map_industry_alias("家电") == "家用电器"
    assert map_industry_alias("新能源") is None
    assert map_industry_alias("金融") == "金融"  # current EM2016 primary label


def test_build_industry_heat_uses_fallback_dedup_and_24h_boundary() -> None:
    popularity = [_pop("000001", 1), _pop("000002", 2), _pop("000003", 3)]
    stocks = {
        "000001": "金融",
        "000002": "金融",
        "000003": "电子",
        "000004": "电子",
    }
    articles = [
        _article("explicit", NOW - timedelta(hours=1), tags=("半导体",)),
        # Explicit tags take precedence; unknown explicit labels do not fall
        # back to a stock mapping.
        _article(
            "explicit-unknown",
            NOW - timedelta(hours=2),
            tags=("新能源",),
            stocks=(StockMention("000003", "000003"),),
        ),
        _article(
            "fallback",
            NOW - timedelta(hours=3),
            stocks=(StockMention("000003", "000003"),),
        ),
        # Same source URL is one article even if it appears twice in a page.
        _article(
            "duplicate",
            NOW - timedelta(hours=4),
            stocks=(StockMention("000003", "000003"),),
            url="https://news.test/same",
        ),
        _article(
            "duplicate-copy",
            NOW - timedelta(hours=4),
            stocks=(StockMention("000003", "000003"),),
            url="https://news.test/same",
        ),
        _article("outside", NOW - timedelta(hours=24, seconds=1), tags=("电子",)),
    ]

    snapshot = build_industry_heat_snapshot(popularity, articles, stocks, window_end=NOW)

    assert snapshot.window_start == NOW - timedelta(hours=24)
    assert snapshot.research_article_total == 4
    assert snapshot.research_article_mapped == 3
    assert snapshot.unmapped_article_count == 1
    assert snapshot.top100_total == 3
    assert snapshot.top100_mapped == 3
    assert snapshot.mapping_coverage == 1.0
    assert [(row.rank, row.industry, row.a, row.b) for row in snapshot.rows] == [
        (1, "金融", 2, 0),
        (2, "电子", 1, 3),
    ]
    assert snapshot.rows[1].article_urls == (
        "https://news.test/explicit",
        "https://news.test/fallback",
        "https://news.test/same",
    )


def test_industry_heat_average_rank_ties_single_industry_and_stable_sort() -> None:
    popularity = [_pop("000001", 1), _pop("000002", 2), _pop("000003", 3)]
    stocks = {"000001": "金融", "000002": "电子", "000003": "传媒"}
    articles = [
        _article("bank", NOW - timedelta(hours=1), tags=("金融",)),
        _article("electronic", NOW - timedelta(hours=1), tags=("电子",)),
    ]
    snapshot = build_industry_heat_snapshot(popularity, articles, stocks, window_end=NOW)
    rows = {row.industry: row for row in snapshot.rows}
    assert rows["传媒"].b == 0
    assert rows["金融"].b_percentile == rows["电子"].b_percentile == 75.0
    assert rows["传媒"].b_percentile == 0.0
    assert [row.industry for row in snapshot.rows] == ["电子", "金融", "传媒"]

    single = build_industry_heat_snapshot(
        [_pop("000001", 1)], [], {"000001": "金融"}, window_end=NOW
    )
    assert single.rows[0].a_percentile == 100.0
    assert single.rows[0].b_percentile == 100.0
    assert single.rows[0].heat == 100.0


def test_industry_heat_snapshot_roundtrip_and_old_snapshot_default() -> None:
    popularity = [_pop("000001", 1)]
    snapshot = build_industry_heat_snapshot(
        popularity, [], {"000001": "银行"}, window_end=NOW, source_complete=False
    )
    restored = type(snapshot).from_dict(snapshot.to_dict())
    assert restored.to_dict() == snapshot.to_dict()
    assert restored.source_status == "partial"

    legacy_payload = {
        "snapshot_id": 1,
        "window_start": (NOW - timedelta(hours=12)).isoformat(),
        "window_end": NOW.isoformat(),
        "created_at": NOW.isoformat(),
        "partial": False,
        "coverages": [],
        "rankings": [],
        "events": [],
        "stats": {},
    }
    legacy = Snapshot.from_dict(legacy_payload)
    assert legacy.industry_heat.to_dict() == {
        "snapshot_at": None,
        "window_start": None,
        "window_end": None,
        "rows": [],
        "top100_total": 0,
        "top100_mapped": 0,
        "mapping_coverage": 0.0,
        "research_article_total": 0,
        "research_article_mapped": 0,
        "unmapped_article_count": 0,
        "mapping_status": "unavailable",
        "source_status": "unavailable",
        "source_error": None,
    }

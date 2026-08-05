from __future__ import annotations

import json
from datetime import datetime, timedelta

from ashare_hotpot.config import SHANGHAI_TZ
from ashare_hotpot.models import (
    OfficialPopularitySnapshot,
    ParsedArticle,
    PopularityRankRow,
    RankingRow,
    Snapshot,
    SourceCoverage,
    StockMention,
)
from ashare_hotpot.storage import Storage


def test_storage_article_and_snapshot_roundtrip(tmp_path) -> None:
    storage = Storage(tmp_path / "hotpot.db")
    now = datetime(2026, 8, 4, 18, 0, tzinfo=SHANGHAI_TZ)
    article = ParsedArticle(
        seq="123",
        url="https://stock.10jqka.com.cn/20260804/c123.shtml",
        title="测试新闻",
        summary="摘要",
        published_at=now,
        channel_key="companynews",
        channel_name="公司资讯",
        source_name="测试来源",
        stocks=(StockMention("000001", "平安银行"),),
    )
    storage.upsert_article(article, now)
    cached = storage.get_cached_article(article.url)
    assert cached is not None
    assert cached.stocks == article.stocks
    assert storage.get_articles_between(now - timedelta(hours=1), now + timedelta(hours=1))[0].title == "测试新闻"

    storage.upsert_stock_industries({"000001": "银行"}, now)
    assert storage.get_stock_industries({"000001", "600519"}) == {"000001": "银行"}

    snapshot = Snapshot(
        snapshot_id=None,
        window_start=now - timedelta(hours=24),
        window_end=now,
        created_at=now,
        partial=True,
        coverages=[SourceCoverage("companynews", "公司资讯", 20, 500, now - timedelta(hours=7), now, False)],
        rankings=[],
        events=[],
        stats={"events": 0},
        popularity=OfficialPopularitySnapshot(
            available=True,
            is_stale=False,
            success_at=now,
            error=None,
            popularity=[
                PopularityRankRow(1, "000001", "平安银行", None, 11.25, 1.5, "https://guba.eastmoney.com/rank/stock?code=000001")
            ],
            surging=[
                PopularityRankRow(3, "600519", "贵州茅台", 2, 1600.0, 2.0, "https://guba.eastmoney.com/rank/stock?code=600519")
            ],
        ),
    )
    saved = storage.save_snapshot(snapshot)
    assert saved.snapshot_id is not None
    loaded = storage.load_latest_snapshot()
    assert loaded is not None
    assert loaded.partial is True
    assert loaded.coverages[0].pages_scanned == 20
    assert loaded.popularity.available is True
    assert loaded.popularity.popularity[0].code == "000001"
    assert loaded.popularity.popularity[0].current_price == 11.25
    assert loaded.popularity.surging[0].change == 2

    legacy_payload = snapshot.to_dict()
    legacy_payload["guba"] = {"available": True, "rankings": []}
    loaded_legacy = Snapshot.from_dict(legacy_payload)
    assert loaded_legacy.popularity.available is True
    assert not hasattr(loaded_legacy, "guba")

    popularity_state = OfficialPopularitySnapshot(
        available=True,
        is_stale=False,
        success_at=now,
        error=None,
        popularity=[PopularityRankRow(1, "000001", "平安银行", None, 11.25, 1.5, "https://guba.eastmoney.com/rank/stock?code=000001")],
        surging=[],
    )
    storage.set_popularity_state(popularity_state, now)
    restored_state = storage.get_popularity_state()
    assert restored_state is not None
    assert restored_state.success_at == now
    assert restored_state.popularity[0].name == "平安银行"

    storage.clear_all()
    assert storage.load_latest_snapshot() is None
    assert storage.get_cached_article(article.url) is None
    assert storage.get_popularity_state() is None


def test_storage_clear_all_removes_stock_industries(tmp_path) -> None:
    storage = Storage(tmp_path / "hotpot.db")
    now = datetime(2026, 8, 4, 18, 0, tzinfo=SHANGHAI_TZ)
    storage.upsert_stock_industries({"000001": "银行"}, now)

    storage.clear_all()

    assert storage.get_stock_industries({"000001"}) == {}


def test_storage_diagnostics_summarize_counts_and_latest_run(tmp_path) -> None:
    storage = Storage(tmp_path / "hotpot.db")
    now = datetime(2026, 8, 4, 18, 0, tzinfo=SHANGHAI_TZ)
    run_id = storage.create_run(now)
    storage.finish_run(run_id, "failed", "测试失败", now + timedelta(seconds=3))
    storage.upsert_article(
        ParsedArticle(
            "1",
            "https://example.test/diagnostics",
            "诊断测试",
            "",
            now,
            "companynews",
            "公司资讯",
            "测试来源",
        ),
        now,
    )

    stats = storage.get_storage_stats()

    assert stats.database_bytes > 0
    assert stats.article_count == 1
    assert stats.snapshot_count == 0
    assert stats.latest_run is not None
    assert stats.latest_run.run_id == run_id
    assert stats.latest_run.status == "failed"
    assert stats.latest_run.message == "测试失败"


def test_migration_clears_legacy_guba_tables_and_snapshot_payload(tmp_path) -> None:
    now = datetime(2026, 8, 4, 18, 0, tzinfo=SHANGHAI_TZ)
    storage = Storage(tmp_path / "hotpot.db")
    legacy_snapshot = Snapshot(
        snapshot_id=None,
        window_start=now - timedelta(hours=24),
        window_end=now,
        created_at=now,
        partial=False,
        coverages=[],
        rankings=[RankingRow(1, "000001", "平安银行", 3, 4, now, ("event-1",), ("银行",))],
        events=[],
        stats={"events": 1},
    ).to_dict()
    legacy_snapshot["guba"] = {
        "available": True,
        "partial": True,
        "rankings": [{"rank": 1, "code": "000001", "name": "平安银行", "post_count": 5, "latest_post": now.isoformat(), "post_ids": ["p1"], "industry_tags": []}],
        "stats": {"bars_total": 1, "bars_scanned": 1, "posts_found": 5},
    }
    news_article = ParsedArticle(
        "a1",
        "https://example.test/news",
        "平安银行公布半年报",
        "",
        now - timedelta(hours=2),
        "companynews",
        "公司资讯",
        "同花顺财经",
        (StockMention("000001", "平安银行"),),
    )
    storage.upsert_article(news_article, now)
    with storage._connect() as connection:
        connection.execute(
            "INSERT INTO guba_posts(post_id, code, stock_name, title, url, published_ts, author, comment_count, fetched_ts) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            ("p1", "000001", "平安银行", "旧股吧帖", "https://example.test/p1", int(now.timestamp()), "股友", 0, int(now.timestamp())),
        )
        connection.execute(
            "INSERT INTO guba_scan_state(code, scanned_ts, pages_scanned, reached_cutoff, error) "
            "VALUES (?, ?, ?, ?, ?)",
            ("000001", int(now.timestamp()), 2, 1, None),
        )
        connection.execute(
            "INSERT INTO guba_stock_catalog(code, name, updated_ts) VALUES (?, ?, ?)",
            ("000001", "平安银行", int(now.timestamp())),
        )
        connection.execute(
            "INSERT INTO snapshots(created_ts, window_start_ts, window_end_ts, partial, payload_json) "
            "VALUES (?, ?, ?, ?, ?)",
            (
                int(now.timestamp()),
                int((now - timedelta(hours=24)).timestamp()),
                int(now.timestamp()),
                0,
                json.dumps(legacy_snapshot, ensure_ascii=False),
            ),
        )

    storage.initialize()

    with storage._connect() as connection:
        assert connection.execute("SELECT COUNT(*) FROM guba_posts").fetchone()[0] == 0
        assert connection.execute("SELECT COUNT(*) FROM guba_scan_state").fetchone()[0] == 0
        assert connection.execute("SELECT COUNT(*) FROM guba_stock_catalog").fetchone()[0] == 0
        payload = json.loads(connection.execute("SELECT payload_json FROM snapshots").fetchone()[0])
    assert "guba" not in payload
    loaded = storage.load_latest_snapshot()
    assert loaded is not None
    assert loaded.popularity.available is False
    assert [row.code for row in loaded.rankings] == ["000001"]
    assert [article.title for article in storage.get_articles_between(now - timedelta(hours=24), now)] == [
        news_article.title
    ]

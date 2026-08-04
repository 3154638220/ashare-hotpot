from __future__ import annotations

from datetime import datetime, timedelta

from ashare_hotpot.config import SHANGHAI_TZ
from ashare_hotpot.models import ParsedArticle, Snapshot, SourceCoverage, StockMention
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
    )
    saved = storage.save_snapshot(snapshot)
    assert saved.snapshot_id is not None
    loaded = storage.load_latest_snapshot()
    assert loaded is not None
    assert loaded.partial is True
    assert loaded.coverages[0].pages_scanned == 20

    storage.clear_all()
    assert storage.load_latest_snapshot() is None
    assert storage.get_cached_article(article.url) is None


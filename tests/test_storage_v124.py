"""Schema 123→124 transparent industry-attribution migration tests."""

from __future__ import annotations

import sqlite3
from datetime import date, datetime, timedelta

import pytest

from ashare_hotpot.config import SHANGHAI_TZ
from ashare_hotpot.models import IndustryHeatRow, IndustryHeatSnapshot, ParsedArticle
from ashare_hotpot.storage import BACKUP_NAME_124, SCHEMA_VERSION, Storage


NOW = datetime(2026, 8, 13, 19, 0, tzinfo=SHANGHAI_TZ)


def test_123_upgrade_adds_attribution_columns_and_one_backup(tmp_path) -> None:
    path = tmp_path / "hotpot.db"
    storage = Storage(path)
    with storage._connect() as connection:
        connection.execute("ALTER TABLE articles DROP COLUMN industry_concepts_json")
        connection.execute("ALTER TABLE industry_heat_rows DROP COLUMN stock_codes_json")
        for column in (
            "explicit_article_count",
            "concept_article_count",
            "stock_fallback_article_count",
            "unknown_label_article_count",
            "unknown_concept_article_count",
            "no_evidence_article_count",
            "stock_industry_unmapped_article_count",
        ):
            connection.execute(f"ALTER TABLE industry_heat_snapshots DROP COLUMN {column}")
        connection.execute("PRAGMA user_version = 123")

    Storage(path)
    backup = path.with_name(BACKUP_NAME_124)
    assert backup.exists()
    before = backup.read_bytes()
    with storage._connect() as connection:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION == 124
        article_columns = {
            str(row[1]) for row in connection.execute("PRAGMA table_info(articles)")
        }
        row_columns = {
            str(row[1])
            for row in connection.execute("PRAGMA table_info(industry_heat_rows)")
        }
        assert "industry_concepts_json" in article_columns
        assert "stock_codes_json" in row_columns
    Storage(path).initialize()
    assert backup.read_bytes() == before


def test_124_migration_failure_rolls_back_to_123(tmp_path, monkeypatch) -> None:
    path = tmp_path / "hotpot.db"
    storage = Storage(path)
    with storage._connect() as connection:
        connection.execute("ALTER TABLE articles DROP COLUMN industry_concepts_json")
        connection.execute("PRAGMA user_version = 123")

    def fail(_connection) -> None:
        _connection.execute("ALTER TABLE articles ADD COLUMN should_rollback TEXT")
        raise sqlite3.OperationalError("fixture failure")

    monkeypatch.setattr(Storage, "_ensure_v124_columns", staticmethod(fail))
    with pytest.raises(sqlite3.OperationalError):
        Storage(path)

    with sqlite3.connect(path) as connection:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 123
        columns = {str(row[1]) for row in connection.execute("PRAGMA table_info(articles)")}
        assert "should_rollback" not in columns
    assert path.with_name(BACKUP_NAME_124).exists()


def test_attribution_and_industry_stocks_roundtrip(tmp_path) -> None:
    storage = Storage(tmp_path / "hotpot.db")
    article = ParsedArticle(
        seq="asic",
        url="https://example.test/asic",
        title="AI ASIC推理场景",
        summary="",
        published_at=NOW,
        channel_key="industry_research",
        channel_name="行业研究",
        source_name="同花顺",
        industry_concepts=("AI ASIC",),
    )
    storage.upsert_article(article, NOW)
    assert storage.get_cached_article(article.url).industry_concepts == ("AI ASIC",)  # type: ignore[union-attr]

    snapshot = IndustryHeatSnapshot(
        snapshot_at=NOW,
        window_start=NOW - timedelta(hours=24),
        window_end=NOW,
        rows=[
            IndustryHeatRow(
                1,
                "电子设备",
                100.0,
                1,
                100.0,
                1,
                100.0,
                article_urls=(article.url,),
                stock_codes=("688167",),
            )
        ],
        top100_total=1,
        top100_mapped=1,
        mapping_coverage=1.0,
        research_article_total=1,
        research_article_mapped=1,
        concept_article_count=1,
        mapping_status="complete",
        source_status="complete",
    )
    assert storage.save_industry_daily_snapshot(snapshot, date(2026, 8, 13))
    restored = storage.get_industry_daily_snapshot(date(2026, 8, 13))
    assert restored is not None
    assert restored.rows[0].stock_codes == ("688167",)
    assert restored.concept_article_count == 1

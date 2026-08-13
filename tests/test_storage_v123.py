"""Schema 122→123 and durable industry-heat history tests."""

from __future__ import annotations

import sqlite3
from datetime import date, datetime, timedelta

import pytest

from ashare_hotpot.config import SHANGHAI_TZ
from ashare_hotpot.models import IndustryHeatRow, IndustryHeatSnapshot, ParsedArticle
from ashare_hotpot.storage import BACKUP_NAME_123, SCHEMA_VERSION, Storage


NOW = datetime(2026, 8, 12, 19, 0, tzinfo=SHANGHAI_TZ)


def _tables(connection: sqlite3.Connection) -> set[str]:
    return {
        str(row[0])
        for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
    }


def _snapshot(*, heat: float = 75.0, complete: bool = True) -> IndustryHeatSnapshot:
    return IndustryHeatSnapshot(
        snapshot_at=NOW,
        window_start=NOW - timedelta(hours=24),
        window_end=NOW,
        rows=[
            IndustryHeatRow(
                rank=1,
                industry="金融",
                heat=heat,
                a=12,
                a_percentile=80.0,
                b=3,
                b_percentile=70.0,
                article_urls=("https://example.test/industry-1",),
            )
        ],
        top100_total=100,
        top100_mapped=99,
        mapping_coverage=99.0,
        research_article_total=4,
        research_article_mapped=3,
        unmapped_article_count=1,
        mapping_status="complete" if complete else "partial",
        source_status="complete" if complete else "partial",
    )


def test_fresh_database_has_schema_123_and_industry_tables(tmp_path) -> None:
    storage = Storage(tmp_path / "hotpot.db")
    with storage._connect() as connection:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION == 124
        assert {"industry_heat_snapshots", "industry_heat_rows"} <= _tables(connection)
        article_columns = {
            str(row[1]) for row in connection.execute("PRAGMA table_info(articles)")
        }
        assert "industry_tags_json" in article_columns


def test_122_upgrade_is_atomic_backed_up_and_idempotent(tmp_path) -> None:
    path = tmp_path / "hotpot.db"
    storage = Storage(path)
    with storage._connect() as connection:
        connection.execute("DROP TABLE industry_heat_rows")
        connection.execute("DROP TABLE industry_heat_snapshots")
        connection.execute("ALTER TABLE articles DROP COLUMN industry_tags_json")
        connection.execute("PRAGMA user_version = 122")

    Storage(path)
    backup = path.with_name(BACKUP_NAME_123)
    assert backup.exists()
    backup_before = backup.read_bytes()
    with storage._connect() as connection:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 124
        assert {"industry_heat_snapshots", "industry_heat_rows"} <= _tables(connection)
        assert "industry_tags_json" in {
            str(row[1]) for row in connection.execute("PRAGMA table_info(articles)")
        }
    Storage(path).initialize()
    assert backup.read_bytes() == backup_before


def test_123_migration_failure_rolls_back_and_keeps_version_122(tmp_path, monkeypatch) -> None:
    path = tmp_path / "hotpot.db"
    storage = Storage(path)
    with storage._connect() as connection:
        connection.execute("DROP TABLE industry_heat_rows")
        connection.execute("DROP TABLE industry_heat_snapshots")
        connection.execute("PRAGMA user_version = 122")
    monkeypatch.setattr(
        "ashare_hotpot.storage.V123_TABLE_STATEMENTS",
        ("CREATE TABLE should_rollback(id INTEGER)", "THIS IS NOT SQL"),
    )

    with pytest.raises(sqlite3.OperationalError):
        Storage(path)

    with sqlite3.connect(path) as connection:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 122
        assert "should_rollback" not in _tables(connection)
    assert path.with_name(BACKUP_NAME_123).exists()


def test_article_industry_tags_roundtrip_and_legacy_default(tmp_path) -> None:
    storage = Storage(tmp_path / "hotpot.db")
    article = ParsedArticle(
        seq="industry-1",
        url="https://example.test/article-1",
        title="行业研究",
        summary="摘要",
        published_at=NOW,
        channel_key="industry_research",
        channel_name="行业研究",
        source_name="同花顺",
        industry_tags=("银行", "金融业"),
    )
    storage.upsert_article(article, NOW)
    restored = storage.get_cached_article(article.url)
    assert restored is not None
    assert restored.industry_tags == article.industry_tags


def test_industry_daily_snapshot_is_complete_and_same_day_immutable(tmp_path) -> None:
    storage = Storage(tmp_path / "hotpot.db")
    complete = _snapshot()
    assert storage.save_industry_daily_snapshot(complete, date(2026, 8, 12)) is True
    assert storage.save_industry_daily_snapshot(_snapshot(heat=99.0), date(2026, 8, 12)) is False
    assert storage.save_industry_daily_snapshot(_snapshot(complete=False), date(2026, 8, 13)) is False

    restored = storage.get_industry_daily_snapshot(date(2026, 8, 12))
    assert restored is not None
    assert restored.rows[0].heat == 75.0
    assert restored.rows[0].article_urls == ("https://example.test/industry-1",)
    assert [item.rows[0].industry for item in storage.get_industry_daily_snapshots()] == ["金融"]
    assert storage.get_storage_stats().industry_daily_snapshot_count == 1

    storage.clear_all()
    assert storage.get_industry_daily_snapshots() == []
    assert storage.get_storage_stats().industry_daily_snapshot_count == 0

"""Schema 121→122 migration and institution warming v2 storage base."""

from __future__ import annotations

import json
import sqlite3
from datetime import date, datetime, timedelta
from pathlib import Path

import pytest

from ashare_hotpot.config import SHANGHAI_TZ
from ashare_hotpot.models import (
    ACTIVITY_DATE_PRECISION_DISCLOSURE_DAY,
    ACTIVITY_DATE_PRECISION_EXPLICIT_DAY,
    ActivityOccurrence,
    Institution,
    ResearchActivity,
    ResearchParticipantOccurrence,
    SourceDocument,
    SourceWindowCoverage,
)
from ashare_hotpot.storage import (
    BACKUP_NAME_122,
    INSTITUTION_METRIC_BATCH_STATE_KEY,
    SCHEMA_VERSION,
    Storage,
)


NOW = datetime(2026, 8, 10, 12, 0, tzinfo=SHANGHAI_TZ)
EXPECTED_V122_TABLES = {
    "activity_occurrences",
    "research_participant_occurrences",
    "source_window_coverages",
}


def _table_names(connection: sqlite3.Connection) -> set[str]:
    return {
        str(row[0])
        for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
    }


def _metric_columns(connection: sqlite3.Connection) -> set[str]:
    return {
        str(row[1])
        for row in connection.execute(
            "PRAGMA table_info(institution_metric_snapshots)"
        ).fetchall()
    }


def _create_121_database(path: Path, *, with_legacy_metric: bool = False) -> None:
    """Create a true schema-121 database from the current additive schema."""

    storage = Storage(path)
    snapshot_ts = int(NOW.timestamp())
    with storage._connect() as connection:
        connection.execute("DROP TABLE IF EXISTS research_participant_occurrences")
        connection.execute("DROP TABLE IF EXISTS activity_occurrences")
        connection.execute("DROP TABLE IF EXISTS source_window_coverages")
        connection.execute(
            "ALTER TABLE institution_metric_snapshots RENAME TO metric_snapshots_122"
        )
        connection.execute(
            """
            CREATE TABLE institution_metric_snapshots (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                stock_code TEXT NOT NULL,
                window_kind TEXT NOT NULL,
                window_start_ts INTEGER,
                window_end_ts INTEGER,
                snapshot_ts INTEGER NOT NULL,
                metrics_json TEXT NOT NULL DEFAULT '{}'
            )
            """
        )
        if with_legacy_metric:
            connection.execute(
                """
                INSERT INTO institution_metric_snapshots(
                    stock_code, window_kind, window_start_ts, window_end_ts,
                    snapshot_ts, metrics_json
                ) VALUES (?, 'z20', ?, ?, ?, ?)
                """,
                (
                    "600000",
                    int((NOW - timedelta(days=20)).timestamp()),
                    snapshot_ts,
                    snapshot_ts,
                    json.dumps({"z20": 2.5}),
                ),
            )
            connection.execute(
                "UPDATE app_state SET value_json=?, updated_ts=? WHERE key=?",
                (
                    json.dumps({"snapshot_ts": snapshot_ts}),
                    snapshot_ts,
                    INSTITUTION_METRIC_BATCH_STATE_KEY,
                ),
            )
        connection.execute("DROP TABLE metric_snapshots_122")
        connection.execute(
            "CREATE INDEX idx_metric_snapshots_window "
            "ON institution_metric_snapshots(window_kind, snapshot_ts)"
        )
        connection.execute(
            "CREATE INDEX idx_metric_snapshots_stock_window "
            "ON institution_metric_snapshots(stock_code, window_kind)"
        )
        connection.execute("PRAGMA user_version = 121")


def _seed_activity(storage: Storage) -> None:
    storage.upsert_source_document(
        SourceDocument(
            document_id="doc-act-1",
            provider_key="irm",
            provider_name="深交所互动易",
            kind="research_activity",
            source_url="https://example.test/doc-act-1",
            document_url=None,
            title="投资者关系活动记录表",
            published_at=NOW,
            stock_codes=("000001",),
            body_text="活动时间：2026年8月8日；参与单位：中信证券、某科技公司",
            content_hash="hash-act-1",
            parse_status="parsed",
            parse_error=None,
        ),
        NOW,
    )
    storage.upsert_research_activity(
        ResearchActivity(
            activity_id="act-1",
            stock_code="000001",
            source_document_id="doc-act-1",
            activity_dates=(date(2026, 8, 8),),
            activity_type="research",
            reported_participant_count=2,
            named_participant_count=2,
            question_count=0,
            high_depth_question_count=0,
            topic_counts={},
        ),
        NOW,
    )
    for institution in (
        Institution(
            institution_id="inst-broker",
            canonical_name="中信证券股份有限公司",
            group_id="group-citic",
            institution_type="brokerage",
            verification_status="verified",
        ),
        Institution(
            institution_id="inst-company",
            canonical_name="某科技有限公司",
            group_id="group-company",
            institution_type="other",
            verification_status="verified",
        ),
    ):
        storage.upsert_institution(institution, NOW)


def test_fresh_database_reports_current_schema_and_occurrence_schema(tmp_path) -> None:
    storage = Storage(tmp_path / "hotpot.db")
    with storage._connect() as connection:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION
        assert EXPECTED_V122_TABLES <= _table_names(connection)
        assert {"metric_version", "source_cohort_id"} <= _metric_columns(connection)


def test_121_upgrade_creates_backup_once_and_preserves_legacy_metric(tmp_path) -> None:
    path = tmp_path / "hotpot.db"
    _create_121_database(path, with_legacy_metric=True)

    storage = Storage(path)
    with storage._connect() as connection:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION
        assert EXPECTED_V122_TABLES <= _table_names(connection)

    backup_path = path.with_name(BACKUP_NAME_122)
    assert backup_path.exists()
    backup_before = backup_path.read_bytes()
    storage.initialize()
    assert backup_path.read_bytes() == backup_before

    # Existing callers still receive the original tuple/payload contract.
    legacy = storage.get_latest_institution_metric_snapshots("z20")
    assert legacy["600000"][1] == {"z20": 2.5}
    record = storage.get_latest_institution_metric_snapshot_records("z20")["600000"]
    assert record.metric_version == "z20_legacy"
    assert record.source_cohort_id == ""


def test_121_migration_failure_rolls_back_schema_and_version(
    tmp_path, monkeypatch
) -> None:
    path = tmp_path / "hotpot.db"
    _create_121_database(path)
    monkeypatch.setattr(
        "ashare_hotpot.storage.V122_TABLE_STATEMENTS",
        ("CREATE TABLE should_rollback(id INTEGER)", "THIS IS NOT SQL"),
    )

    with pytest.raises(sqlite3.OperationalError):
        Storage(path)

    connection = sqlite3.connect(path)
    try:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 121
        assert "should_rollback" not in _table_names(connection)
        assert "metric_version" not in _metric_columns(connection)
    finally:
        connection.close()
    assert path.with_name(BACKUP_NAME_122).exists()

    monkeypatch.undo()
    assert Storage(path).get_storage_stats().activity_occurrence_count == 0


def test_occurrence_replace_is_atomic_and_preserves_excluded_organisations(
    tmp_path,
) -> None:
    storage = Storage(tmp_path / "hotpot.db")
    _seed_activity(storage)
    occurrences = [
        ActivityOccurrence(
            occurrence_id="occ-day",
            activity_id="act-1",
            occurred_on=date(2026, 8, 8),
            period_start=date(2026, 8, 8),
            period_end=date(2026, 8, 8),
            date_precision=ACTIVITY_DATE_PRECISION_EXPLICIT_DAY,
            metric_eligible=True,
            exclusion_reason=None,
            evidence_id="ev-date",
            parse_version="warming-v2",
        ),
        ActivityOccurrence(
            occurrence_id="occ-disclosure",
            activity_id="act-1",
            occurred_on=date(2026, 8, 10),
            period_start=None,
            period_end=None,
            date_precision=ACTIVITY_DATE_PRECISION_DISCLOSURE_DAY,
            metric_eligible=False,
            exclusion_reason="仅有披露日",
            evidence_id=None,
            parse_version="warming-v2",
        ),
    ]
    participants = [
        ResearchParticipantOccurrence(
            participant_occurrence_id="po-broker",
            activity_occurrence_id="occ-day",
            activity_id="act-1",
            institution_id="inst-broker",
            analyst_name="王明",
            research_eligible=True,
            eligibility_reason="券商研究部门",
            evidence_id="ev-broker",
            parse_version="warming-v2",
        ),
        ResearchParticipantOccurrence(
            participant_occurrence_id="po-company",
            activity_occurrence_id="occ-day",
            activity_id="act-1",
            institution_id="inst-company",
            analyst_name="王明",
            research_eligible=False,
            eligibility_reason="产业公司，仅保留明细",
            evidence_id="ev-company",
            parse_version="warming-v2",
        ),
    ]
    invalid_metric_day = ActivityOccurrence(
        occurrence_id="occ-invalid",
        activity_id="act-1",
        occurred_on=date(2026, 8, 10),
        period_start=None,
        period_end=None,
        date_precision=ACTIVITY_DATE_PRECISION_DISCLOSURE_DAY,
        metric_eligible=True,
        exclusion_reason=None,
        evidence_id=None,
        parse_version="warming-v2",
    )
    with pytest.raises(ValueError, match="明确到日"):
        storage.replace_research_occurrences("act-1", [invalid_metric_day], [])
    assert storage.get_activity_occurrences("act-1") == []

    storage.replace_research_occurrences("act-1", occurrences, participants)

    assert len(storage.get_activity_occurrences("act-1")) == 2
    assert [item.occurrence_id for item in storage.get_activity_occurrences(
        "act-1", metric_eligible_only=True
    )] == ["occ-day"]
    assert len(storage.get_research_participant_occurrences("act-1")) == 2
    eligible = storage.get_research_participant_occurrences(
        "act-1", research_eligible_only=True
    )
    assert [(item.institution_id, item.analyst_name) for item in eligible] == [
        ("inst-broker", "王明")
    ]

    # Invalid replacement is rejected before the transaction touches old rows.
    invalid = ResearchParticipantOccurrence(
        participant_occurrence_id="po-invalid",
        activity_occurrence_id="missing-occurrence",
        activity_id="act-1",
        institution_id="inst-broker",
        analyst_name=None,
        research_eligible=True,
        eligibility_reason="券商研究部门",
        evidence_id=None,
        parse_version="warming-v2",
    )
    with pytest.raises(ValueError):
        storage.replace_research_occurrences("act-1", occurrences, [invalid])
    assert len(storage.get_research_participant_occurrences("act-1")) == 2


def test_source_window_coverage_and_metric_metadata_roundtrip(tmp_path) -> None:
    storage = Storage(tmp_path / "hotpot.db")
    coverage = SourceWindowCoverage(
        source_key="irm_ircs",
        market="sz",
        source_kind="research_activity",
        window_kind="warming_20",
        source_cohort_id="research-sz-v1",
        requested_start=date(2025, 8, 1),
        requested_end=date(2026, 8, 10),
        covered_start=date(2026, 5, 13),
        covered_end=date(2026, 8, 8),
        reached_cutoff=True,
        reconciled=True,
        cohort_eligible=False,
        last_success_at=NOW,
        error=None,
        exclusion_reason="尚未覆盖当前与十二个历史桶",
        updated_at=NOW,
    )
    storage.upsert_source_window_coverage(coverage)
    assert storage.get_source_window_coverages(
        market="sz", source_cohort_id="research-sz-v1"
    ) == [coverage]

    invalid_future_coverage = SourceWindowCoverage(
        source_key="irm_ircs",
        market="sz",
        source_kind="research_activity",
        window_kind="warming_20",
        source_cohort_id="invalid-future",
        requested_start=date(2025, 8, 1),
        requested_end=date(2026, 8, 10),
        covered_start=date(2025, 8, 1),
        covered_end=date(2026, 8, 11),
        reached_cutoff=True,
        reconciled=True,
        cohort_eligible=True,
        last_success_at=NOW,
        error=None,
        exclusion_reason=None,
        updated_at=NOW,
    )
    with pytest.raises(ValueError, match="不得晚于请求结束日"):
        storage.upsert_source_window_coverage(invalid_future_coverage)

    storage.upsert_institution_metric_snapshot(
        stock_code="000001",
        window_kind="warming_20",
        metrics={"warming_score": 1.25, "z20_legacy": 2.0},
        window_start=NOW - timedelta(days=20),
        window_end=NOW,
        snapshot_at=NOW,
        metric_version="warming_v2",
        source_cohort_id="research-sz-v1",
    )
    record = storage.get_latest_institution_metric_snapshot_records(
        "warming_20"
    )["000001"]
    assert record.metric_version == "warming_v2"
    assert record.source_cohort_id == "research-sz-v1"
    assert record.metrics["z20_legacy"] == 2.0

    stats = storage.get_storage_stats()
    assert stats.source_window_coverage_count == 1
    storage.clear_all()
    stats = storage.get_storage_stats()
    assert stats.activity_occurrence_count == 0
    assert stats.participant_occurrence_count == 0
    assert stats.source_window_coverage_count == 0


def test_activity_delete_cascades_occurrence_rows(tmp_path) -> None:
    storage = Storage(tmp_path / "hotpot.db")
    _seed_activity(storage)
    occurrence = ActivityOccurrence(
        occurrence_id="occ-1",
        activity_id="act-1",
        occurred_on=date(2026, 8, 8),
        period_start=date(2026, 8, 8),
        period_end=date(2026, 8, 8),
        date_precision=ACTIVITY_DATE_PRECISION_EXPLICIT_DAY,
        metric_eligible=True,
        exclusion_reason=None,
        evidence_id=None,
        parse_version="warming-v2",
    )
    participant = ResearchParticipantOccurrence(
        participant_occurrence_id="po-1",
        activity_occurrence_id="occ-1",
        activity_id="act-1",
        institution_id="inst-broker",
        analyst_name=None,
        research_eligible=True,
        eligibility_reason="券商研究部门",
        evidence_id=None,
        parse_version="warming-v2",
    )
    storage.replace_research_occurrences("act-1", [occurrence], [participant])
    with storage._connect() as connection:
        connection.execute(
            "DELETE FROM research_activities WHERE activity_id='act-1'"
        )
    assert storage.get_activity_occurrences("act-1") == []
    assert storage.get_research_participant_occurrences("act-1") == []


def test_schema_constant_is_124() -> None:
    assert SCHEMA_VERSION == 124

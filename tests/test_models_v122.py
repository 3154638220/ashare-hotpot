"""Institution warming v2 occurrence/coverage model contracts (schema 122)."""

from __future__ import annotations

from datetime import date, datetime

from ashare_hotpot.config import SHANGHAI_TZ
from ashare_hotpot.models import (
    ACTIVITY_DATE_PRECISION_DISCLOSURE_DAY,
    ACTIVITY_DATE_PRECISION_EXPLICIT_DAY,
    ACTIVITY_DATE_PRECISION_EXPLICIT_RANGE,
    ACTIVITY_DATE_PRECISION_UNKNOWN,
    ACTIVITY_DATE_PRECISIONS,
    ActivityOccurrence,
    InstitutionMetricSnapshotRecord,
    ResearchParticipantOccurrence,
    SourceWindowCoverage,
)


NOW = datetime(2026, 8, 10, 12, 0, tzinfo=SHANGHAI_TZ)


def test_activity_occurrence_roundtrip_preserves_date_quality() -> None:
    occurrence = ActivityOccurrence(
        occurrence_id="occ-1",
        activity_id="act-1",
        occurred_on=date(2026, 8, 8),
        period_start=date(2026, 8, 8),
        period_end=date(2026, 8, 8),
        date_precision=ACTIVITY_DATE_PRECISION_EXPLICIT_DAY,
        metric_eligible=True,
        exclusion_reason=None,
        evidence_id="ev-date-1",
        parse_version="warming-v2-20260810",
    )
    assert ActivityOccurrence.from_dict(occurrence.to_dict()) == occurrence


def test_activity_occurrence_range_and_disclosure_precision_are_fixed() -> None:
    assert ACTIVITY_DATE_PRECISIONS == (
        ACTIVITY_DATE_PRECISION_EXPLICIT_DAY,
        ACTIVITY_DATE_PRECISION_EXPLICIT_RANGE,
        ACTIVITY_DATE_PRECISION_DISCLOSURE_DAY,
        ACTIVITY_DATE_PRECISION_UNKNOWN,
    )
    occurrence = ActivityOccurrence.from_dict(
        {
            "occurrence_id": "occ-range",
            "activity_id": "act-1",
            "period_start": "2026-08-01",
            "period_end": "2026-08-03",
            "date_precision": ACTIVITY_DATE_PRECISION_EXPLICIT_RANGE,
            "exclusion_reason": "仅披露活动区间，无法映射机构到具体日期",
        }
    )
    assert occurrence.occurred_on is None
    assert occurrence.metric_eligible is False


def test_participant_occurrence_roundtrip_keeps_institution_name_pair() -> None:
    occurrence = ResearchParticipantOccurrence(
        participant_occurrence_id="po-1",
        activity_occurrence_id="occ-1",
        activity_id="act-1",
        institution_id="inst-broker",
        analyst_name="王明",
        research_eligible=True,
        eligibility_reason="券商研究部门",
        evidence_id="ev-participant-1",
        parse_version="warming-v2-20260810",
    )
    assert ResearchParticipantOccurrence.from_dict(occurrence.to_dict()) == occurrence


def test_source_window_coverage_roundtrip_keeps_cohort_state() -> None:
    coverage = SourceWindowCoverage(
        source_key="sse_publish",
        market="sh",
        source_kind="research_activity",
        window_kind="warming_20",
        source_cohort_id="research-sh-v1",
        requested_start=date(2025, 8, 1),
        requested_end=date(2026, 8, 10),
        covered_start=date(2025, 8, 1),
        covered_end=date(2026, 8, 10),
        reached_cutoff=True,
        reconciled=True,
        cohort_eligible=True,
        last_success_at=NOW,
        error=None,
        exclusion_reason=None,
        updated_at=NOW,
    )
    assert SourceWindowCoverage.from_dict(coverage.to_dict()) == coverage


def test_metric_snapshot_record_roundtrip_and_legacy_defaults() -> None:
    record = InstitutionMetricSnapshotRecord(
        stock_code="600000",
        window_kind="z20",
        window_start=NOW,
        window_end=NOW,
        snapshot_at=NOW,
        metrics={"z20": 1.5},
        metric_version="warming_v2",
        source_cohort_id="research-sh-v1",
    )
    assert InstitutionMetricSnapshotRecord.from_dict(record.to_dict()) == record

    legacy = InstitutionMetricSnapshotRecord.from_dict(
        {
            "stock_code": "600000",
            "window_kind": "z20",
            "snapshot_at": NOW.isoformat(),
            "metrics": {"z20": 1.5},
        }
    )
    assert legacy.metric_version == "z20_legacy"
    assert legacy.source_cohort_id == ""

from __future__ import annotations

import csv
from datetime import date, datetime, timedelta

from ashare_hotpot.config import AppSettings, SHANGHAI_TZ
from ashare_hotpot.exporting import export_csv, row_values, tab_separated_row
from ashare_hotpot.institution_metrics import (
    ResearchBoardService,
    _ActivityData,
    sort_warming_v2,
    warming_v2_rows,
    warming_v2_statistics,
)
from ashare_hotpot.models import (
    ResearchActivity,
    ResearchCoverage,
    ResearchSourceCohort,
    SourceDocument,
    WarmingV2Row,
)
from ashare_hotpot.research_activities import ActivityParseResult
from ashare_hotpot.institutions import InstitutionRegistry
from ashare_hotpot.research_activities import parse_research_activity
from ashare_hotpot.research_views import (
    load_institution_detail,
    load_persistence_rows,
    load_z20_rows,
)
from ashare_hotpot.storage import Storage
from scripts.evaluation.compare_warming_metrics import build_report


NOW = datetime(2026, 8, 10, 18, 0, tzinfo=SHANGHAI_TZ)


def _weekdays(end: date, count: int) -> list[date]:
    rows: list[date] = []
    current = end
    while len(rows) < count:
        if current.weekday() < 5:
            rows.append(current)
        current -= timedelta(days=1)
    return list(reversed(rows))


def _coverage(days: list[date]) -> ResearchCoverage:
    cohort = ResearchSourceCohort(
        market="sz",
        window_kind="warming_20",
        source_cohort_id="warming-v2-sz-release",
        source_keys=("cninfo_research",),
        supplemental_source_keys=(),
        unavailable_source_keys=(),
        requested_start=days[0],
        requested_end=days[-1],
        covered_start=days[0],
        covered_end=days[-1],
        trading_days_covered=len(days),
        formal_ranking=True,
    )
    return ResearchCoverage(
        requested_start=days[0],
        covered_start=days[0],
        covered_end=days[-1],
        trading_days_covered=len(days),
        sources_scanned=1,
        sources_total=1,
        reached_cutoff=True,
        calendar_fallback=False,
        last_success_at=NOW,
        provisional=False,
        error=None,
        market_cohorts=(cohort,),
        formal_ranking=True,
    )


def test_release_default_is_v2_with_explicit_legacy_rollback(tmp_path) -> None:
    settings = AppSettings(app_root=tmp_path)
    assert settings.institution_metric_version == "warming_v2"
    settings.institution_metric_version = "z20_legacy"
    assert settings.institution_metric_version == "z20_legacy"


def test_same_day_range_and_day_normalize_to_one_occurrence(tmp_path) -> None:
    storage = Storage(tmp_path / "hotpot.db")
    storage.initialize()
    document = SourceDocument(
        document_id="doc-same-day-range",
        provider_key="irm",
        provider_name="互动易",
        kind="research_activity",
        source_url="https://example.test/same-day",
        document_url=None,
        title="投资者关系活动记录表",
        published_at=NOW,
        stock_codes=("000001",),
        body_text=(
            "活动时间：2026年7月21日\n"
            "活动安排：2026年7月21日至2026年7月21日\n"
            "参与机构：中信证券研究部"
        ),
        content_hash="same-day",
        parse_status="parsed",
        parse_error=None,
    )
    parsed = parse_research_activity(
        document, InstitutionRegistry(storage), pipeline_version="v2"
    )
    assert parsed is not None
    eligible = [item for item in parsed.activity_occurrences if item.metric_eligible]
    assert len(eligible) == 1
    assert eligible[0].occurred_on == date(2026, 7, 21)


def test_frozen_sparse_zero_variance_outlier_empty_and_recency_cases() -> None:
    zero_variance = warming_v2_statistics(100, [100] * 12)
    assert zero_variance.baseline_variance == 0.0
    assert zero_variance.score == 0.0
    sparse = warming_v2_statistics(1, [0] * 12)
    assert sparse.predictive_variance == 1.0833
    assert sparse.score == 0.961
    outlier = warming_v2_statistics(1, [0] * 11 + [100])
    assert outlier.baseline_variance is not None
    assert outlier.baseline_variance > 800
    assert abs(outlier.score or 0.0) < 1.0

    days = _weekdays(NOW.date(), 260)
    assert warming_v2_rows([], days, _coverage(days), {},) == []
    base = WarmingV2Row(
        stock_code="000001",
        industry=None,
        warming_score=1.0,
        baseline_mean=0.0,
        baseline_variance=0.0,
        predictive_variance=1.0,
        baseline_bucket_count=12,
        coverage_level="full",
        absolute_change=1.0,
        current_unique_groups=1,
        unseen_100d_groups=1,
        active_days=1,
        single_day_concentration=1.0,
        single_day=True,
        recent_activity=days[-2],
        industry_percentile=None,
        industry_sample_size=0,
        source_cohort_id="warming-v2-sz-release",
    )
    newer = WarmingV2Row.from_dict(
        {**base.to_dict(), "stock_code": "000002", "recent_activity": days[-1].isoformat()}
    )
    assert [row.stock_code for row in sort_warming_v2([base, newer])] == [
        "000002",
        "000001",
    ]


def test_ui_csv_copy_and_detail_share_v2_provenance(tmp_path) -> None:
    storage = Storage(tmp_path / "hotpot.db")
    storage.initialize()
    days = _weekdays(NOW.date(), 260)
    coverage = _coverage(days)
    cohort_id = coverage.market_cohorts[0].source_cohort_id
    storage.upsert_institution_metric_snapshot(
        stock_code="000001",
        window_kind="warming_20",
        metrics={
            "warming_score": 2.5,
            "absolute_change": 3.0,
            "current_unique_groups": 4,
            "unseen_100d_groups": 3,
            "active_days": 2,
            "single_day_concentration": 0.75,
            "single_day": False,
            "recent_activity": days[-1].isoformat(),
            "coverage_level": "full",
            "date_quality": "explicit_day",
            "excluded_organization_count": 2,
            "provisional_reason": None,
        },
        window_start=NOW - timedelta(days=20),
        window_end=NOW,
        snapshot_at=NOW,
        metric_version="warming_v2",
        source_cohort_id=cohort_id,
    )
    storage.upsert_institution_metric_snapshot(
        stock_code="000001",
        window_kind="persistence_60_v2",
        metrics={
            "persistence_score": None,
            "active_weeks": 2,
            "active_week_ratio": 0.2,
            "unique_groups": 4,
            "repeat_followup_ratio": 0.25,
            "depth_score": None,
            "single_day_concentration": 0.75,
            "topics": {},
            "covered_trading_days": 60,
            "provisional": True,
            "question_data_status": "missing_body",
            "date_mapping_complete": True,
            "date_quality": "explicit_day",
            "excluded_organization_count": 2,
            "provisional_reason": "问答正文缺失",
        },
        window_start=NOW - timedelta(days=90),
        window_end=NOW,
        snapshot_at=NOW,
        metric_version="persistence_rules_v2",
        source_cohort_id=cohort_id,
    )
    warming_rows = load_z20_rows(
        storage, coverage=coverage, metric_version="warming_v2"
    )
    assert warming_rows[0].metric_version == "warming_v2"
    assert warming_rows[0].source_cohort_id == cohort_id
    assert warming_rows[0].date_quality == "explicit_day"
    assert warming_rows[0].excluded_organization_count == 2
    values = row_values("z20", warming_rows[0])
    assert "warming_v2" in values and cohort_id in values
    assert tab_separated_row("z20", warming_rows[0]).split("\t") == [
        str(value) for value in values
    ]
    output = tmp_path / "warming.csv"
    export_csv(output, "z20", warming_rows)
    with output.open(encoding="utf-8-sig", newline="") as stream:
        csv_rows = list(csv.reader(stream))
    assert "标准化升温值（描述性）" in csv_rows[0]
    assert "指标版本" in csv_rows[0]
    assert csv_rows[1][12] == "warming_v2"

    persistence = load_persistence_rows(
        storage,
        "persistence_60",
        coverage=coverage,
        metric_version="warming_v2",
    )[0]
    assert persistence.persistence_score is None
    assert persistence.provisional_reason == "问答正文缺失"
    assert "persistence_rules_v2" in row_values("persist60", persistence)
    detail = load_institution_detail(
        storage,
        "000001",
        "000001",
        "warming_20",
        start_date=days[0],
        end_date=days[-1],
        coverage=coverage,
    )
    assert detail.metrics["metric_version"] == "warming_v2"
    assert detail.metrics["source_cohort_id"] == cohort_id


def test_550_day_parse_failure_does_not_publish_staged_activity(tmp_path, monkeypatch) -> None:
    storage = Storage(tmp_path / "hotpot.db")
    storage.initialize()
    for document_id in ("doc-a", "doc-b"):
        storage.upsert_source_document(
            SourceDocument(
                document_id=document_id,
                provider_key="cninfo",
                provider_name="巨潮资讯",
                kind="research_activity",
                source_url=f"https://example.test/{document_id}",
                document_url=None,
                title="投资者关系活动记录表",
                published_at=NOW - timedelta(days=10),
                stock_codes=("000001",),
                body_text="活动时间：2026年8月1日\n参与机构：中信证券研究部",
                content_hash=document_id,
                parse_status="parsed",
                parse_error=None,
            ),
            NOW,
        )
    old = ResearchActivity(
        activity_id="activity-a",
        stock_code="000001",
        source_document_id="doc-a",
        activity_dates=(date(2026, 7, 1),),
        activity_type="other",
        reported_participant_count=None,
        named_participant_count=0,
        question_count=0,
        high_depth_question_count=0,
        topic_counts={},
    )
    storage.upsert_research_activity(old, NOW - timedelta(days=1))
    storage.upsert_institution_metric_snapshot(
        stock_code="000001",
        window_kind="z20",
        metrics={"z20": 1.0},
        window_start=None,
        window_end=NOW - timedelta(days=1),
        snapshot_at=NOW - timedelta(days=1),
    )

    staged = ActivityParseResult(
        activity=ResearchActivity(
            **{
                **old.to_dict(),
                "activity_dates": (date(2026, 8, 1),),
                "activity_type": "onsite_research",
            }
        ),
        participants=(),
        evidence_refs=(),
    )

    def fake_parse(document, _registry, *, pipeline_version):
        assert pipeline_version == "v2"
        if document.document_id == "doc-b":
            raise RuntimeError("fixture parser failure")
        return staged

    monkeypatch.setattr(
        "ashare_hotpot.institution_metrics.parse_research_activity", fake_parse
    )
    result = ResearchBoardService(
        AppSettings(app_root=tmp_path), storage
    ).run(now=NOW, backfill_days=550, pipeline_version="v2")
    assert result.errors
    assert result.activities_persisted == 0
    assert storage.get_research_activity("activity-a") == old
    assert storage.get_latest_institution_metric_snapshots("z20")["000001"][1] == {
        "z20": 1.0
    }


def test_readonly_comparison_report_has_required_release_metrics(tmp_path) -> None:
    storage = Storage(tmp_path / "hotpot.db")
    storage.initialize()
    storage.upsert_institution_metric_snapshot(
        stock_code="000001",
        window_kind="z20",
        metrics={"z20": 1.0, "new_groups": 1},
        window_start=None,
        window_end=NOW,
        snapshot_at=NOW,
    )
    storage.upsert_institution_metric_snapshot(
        stock_code="000001",
        window_kind="warming_20",
        metrics={
            "warming_score": 2.0,
            "coverage_level": "full",
            "single_day": True,
        },
        window_start=None,
        window_end=NOW,
        snapshot_at=NOW,
        metric_version="warming_v2",
        source_cohort_id="warming-v2-sz-release",
    )
    report = build_report(tmp_path / "hotpot.db")
    assert report["top20"]["overlap_count"] == 1
    assert "non_research_organization_pollution" in report
    assert "date_corrections" in report
    assert report["single_day_concentration"]["share"] == 1.0
    assert "未做回测" in report["note"]

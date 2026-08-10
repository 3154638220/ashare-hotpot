from __future__ import annotations

from datetime import date, datetime, timedelta

import pytest

from ashare_hotpot.config import AppSettings, SHANGHAI_TZ
from ashare_hotpot.institution_metrics import (
    ResearchBoardService,
    WarmingStatistics,
    _ActivityData,
    sort_warming_v2,
    warming_v2_rows,
    warming_v2_statistics,
)
from ashare_hotpot.models import (
    ResearchCoverage,
    ResearchSourceCohort,
    SyncCursor,
    WarmingV2Row,
)
from ashare_hotpot.storage import Storage


NOW = datetime(2026, 8, 10, 18, 0, tzinfo=SHANGHAI_TZ)


def _weekdays(end: date, count: int) -> list[date]:
    days: list[date] = []
    current = end
    while len(days) < count:
        if current.weekday() < 5:
            days.append(current)
        current -= timedelta(days=1)
    return list(reversed(days))


def _cohort_coverage(
    days: list[date],
    *,
    covered_start: date | None = None,
    formal: bool = True,
) -> ResearchCoverage:
    start = covered_start or days[0]
    cohort = ResearchSourceCohort(
        market="sz",
        window_kind="warming_20",
        source_cohort_id="warming-v2-sz-test",
        source_keys=("cninfo_research",),
        supplemental_source_keys=("irm_ircs",),
        unavailable_source_keys=(),
        requested_start=days[0],
        requested_end=days[-1],
        covered_start=start,
        covered_end=days[-1],
        trading_days_covered=len([item for item in days if item >= start]),
        formal_ranking=formal,
    )
    return ResearchCoverage(
        requested_start=days[0],
        covered_start=start,
        covered_end=days[-1],
        trading_days_covered=cohort.trading_days_covered,
        sources_scanned=1,
        sources_total=2,
        reached_cutoff=formal,
        calendar_fallback=False,
        last_success_at=NOW,
        provisional=not formal,
        error=None,
        market_cohorts=(cohort,),
        formal_ranking=formal,
    )


def _activity(
    activity_id: str,
    day: date,
    groups: tuple[str, ...],
    *,
    stock_code: str = "000001",
) -> _ActivityData:
    return _ActivityData(
        activity_id=activity_id,
        stock_code=stock_code,
        activity_dates=(day,),
        end_date=day,
        groups=frozenset(groups),
        analysts=frozenset(),
        question_count=0,
        high_depth_question_count=0,
        depth_counts={},
        topics={},
        type_counts={},
        group_dates={group: frozenset((day,)) for group in groups},
        source_key="cninfo_research",
    )


def _full_activity_series(days: list[date]) -> list[_ActivityData]:
    rows = [
        _activity(f"history-{index}", days[index * 20 + 10], ("baseline",))
        for index in range(12)
    ]
    rows.extend(
        (
            _activity("current-a", days[-2], ("new-a", "new-b", "new-c")),
            _activity("current-b", days[-1], ("new-d",)),
        )
    )
    return rows


def test_warming_formula_uses_sample_variance_and_predictive_variance() -> None:
    stats = warming_v2_statistics(9, [1, 2, 3, 4, 5])
    assert stats == WarmingStatistics(
        score=3.162,
        baseline_mean=3.0,
        baseline_variance=2.5,
        predictive_variance=3.6,
        absolute_change=6.0,
        baseline_bucket_count=5,
    )
    assert warming_v2_statistics(4, [1, 2, 3, 4]).score is None


def test_full_warming_row_exposes_absolute_and_concentration_metrics() -> None:
    days = _weekdays(NOW.date(), 260)
    rows = warming_v2_rows(
        _full_activity_series(days),
        days,
        _cohort_coverage(days),
        {"000001": "银行"},
    )
    assert len(rows) == 1
    row = rows[0]
    assert row.coverage_level == "full"
    assert row.baseline_bucket_count == 12
    assert row.baseline_mean == 1.0
    assert row.baseline_variance == 0.0
    assert row.predictive_variance == pytest.approx(1.0833)
    assert row.warming_score == pytest.approx(2.882)
    assert row.absolute_change == 3.0
    assert row.current_unique_groups == 4
    assert row.unseen_100d_groups == 4
    assert row.active_days == 2
    assert row.single_day_concentration == 0.75
    assert row.single_day is False
    assert row.industry_percentile is None


def test_five_to_eleven_buckets_are_provisional_and_fewer_than_five_raw() -> None:
    days = _weekdays(NOW.date(), 260)
    activities = _full_activity_series(days)
    buckets = [days[index : index + 20] for index in range(0, 260, 20)]
    provisional = warming_v2_rows(
        activities,
        days,
        _cohort_coverage(days, covered_start=buckets[-7][0], formal=False),
        {"000001": "银行"},
    )[0]
    assert provisional.coverage_level == "provisional"
    assert provisional.baseline_bucket_count == 6
    assert provisional.warming_score is not None

    raw = warming_v2_rows(
        activities,
        days,
        _cohort_coverage(days, covered_start=buckets[-5][0], formal=False),
        {"000001": "银行"},
    )[0]
    assert raw.coverage_level == "raw_only"
    assert raw.baseline_bucket_count == 4
    assert raw.warming_score is None
    assert raw.absolute_change is None
    assert raw.unseen_100d_groups is None


def test_sort_order_and_complete_zero_activity_industry_universe() -> None:
    days = _weekdays(NOW.date(), 260)
    universe = {f"000{index:03d}": "测试行业" for index in range(1, 21)}
    rows = warming_v2_rows(
        _full_activity_series(days),
        days,
        _cohort_coverage(days),
        {"000001": "测试行业"},
        complete_industry_universe=universe,
    )
    # The denominator contains all 20 listed-company members; 19 have zero
    # activity and are not emitted as board rows.
    assert rows[0].industry_sample_size == 20
    assert rows[0].industry_percentile == 97.5

    base = rows[0]
    provisional = WarmingV2Row.from_dict(
        {
            **base.to_dict(),
            "stock_code": "000002",
            "coverage_level": "provisional",
            "warming_score": 99.0,
        }
    )
    tied_recent = WarmingV2Row.from_dict(
        {
            **base.to_dict(),
            "stock_code": "000003",
            "warming_score": base.warming_score,
            "recent_activity": (days[-1] - timedelta(days=1)).isoformat(),
        }
    )
    ordered = sort_warming_v2([provisional, tied_recent, base])
    assert [item.stock_code for item in ordered] == ["000001", "000003", "000002"]
    assert WarmingV2Row.from_dict(base.to_dict()) == base


def test_service_publishes_warming_v2_alongside_legacy(monkeypatch, tmp_path) -> None:
    storage = Storage(tmp_path / "hotpot.db")
    storage.initialize()
    days = _weekdays(NOW.date(), 260)
    for year in sorted({item.year for item in days}):
        storage.replace_trading_days(
            year,
            [item for item in days if item.year == year],
            source="sse",
            updated_at=NOW,
        )
    storage.save_sync_state(
        SyncCursor(
            source_key="cninfo_research",
            sync_kind="research_activity",
            cursor={"covered_end": days[-1].isoformat()},
            target_start=days[0],
            covered_start=days[0],
            last_success_at=NOW,
            last_error=None,
            updated_at=NOW,
        )
    )
    activities = _full_activity_series(days)

    def fake_load(_storage, start, end, **_kwargs):
        return [item for item in activities if start <= item.end_date <= end]

    monkeypatch.setattr(
        "ashare_hotpot.institution_metrics._load_activity_data", fake_load
    )
    result = ResearchBoardService(
        AppSettings(app_root=tmp_path), storage
    ).run(now=NOW)
    assert result.z20_rows
    assert result.warming_v2_rows
    record = storage.get_latest_institution_metric_snapshot_records(
        "warming_20"
    )["000001"]
    assert record.metric_version == "warming_v2"
    assert record.source_cohort_id == result.warming_v2_rows[0].source_cohort_id
    assert record.metrics["coverage_level"] == "full"

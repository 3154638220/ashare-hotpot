from __future__ import annotations

from datetime import date, datetime, timedelta

from ashare_hotpot.config import AppSettings, SHANGHAI_TZ
from ashare_hotpot.institution_coverage import (
    MARKET_RESEARCH_SOURCE_KEYS,
    applicable_research_sources,
    build_institution_research_coverage,
    build_research_source_cohorts,
    stock_market,
)
from ashare_hotpot.models import ResearchCoverage, ResearchSourceCohort, SyncCursor
from ashare_hotpot.research_views import z20_view_meta
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


def _storage_with_calendar(tmp_path) -> tuple[Storage, list[date]]:
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
    return storage, days


def _sync(
    storage: Storage,
    source_key: str,
    *,
    covered_start: date,
    covered_end: date,
    error: str | None = None,
) -> None:
    storage.save_sync_state(
        SyncCursor(
            source_key=source_key,
            sync_kind="research_activity",
            cursor={"page": 9, "covered_end": covered_end.isoformat()},
            target_start=covered_start,
            covered_start=covered_start,
            last_success_at=NOW,
            last_error=error,
            updated_at=NOW,
        )
    )


def _by_market(cohorts: tuple[ResearchSourceCohort, ...], market: str):
    return next(item for item in cohorts if item.market == market)


def test_market_applicability_excludes_all_announcement_sources(tmp_path) -> None:
    settings = AppSettings(app_root=tmp_path)
    assert MARKET_RESEARCH_SOURCE_KEYS == {
        "sh": ("sse_publish",),
        "sz": ("cninfo_research", "irm_ircs"),
        "bj": ("bse_performance",),
    }
    applicable = {
        market: tuple(item.key for item in applicable_research_sources(settings, market))
        for market in ("sh", "sz", "bj")
    }
    assert applicable == MARKET_RESEARCH_SOURCE_KEYS
    assert not {
        "cninfo_announcement",
        "sse_announcement",
        "bse_announcement",
    } & {key for keys in applicable.values() for key in keys}
    assert stock_market("600519") == "sh"
    assert stock_market("300750") == "sz"
    assert stock_market("920001") == "bj"
    assert stock_market("not-a-code") is None


def test_new_short_history_source_is_supplemental_then_joins_cohort(tmp_path) -> None:
    storage, days = _storage_with_calendar(tmp_path)
    settings = AppSettings(app_root=tmp_path)
    _sync(
        storage,
        "cninfo_research",
        covered_start=days[0] - timedelta(days=10),
        covered_end=NOW.date() + timedelta(days=5),
    )
    _sync(
        storage,
        "irm_ircs",
        covered_start=days[180],
        covered_end=NOW.date(),
    )

    first = _by_market(
        build_research_source_cohorts(settings, storage, now=NOW), "sz"
    )
    assert first.source_keys == ("cninfo_research",)
    assert first.supplemental_source_keys == ("irm_ircs",)
    assert first.covered_start == days[0] - timedelta(days=10)
    # Source cursors may contain a future document date, but a metric window
    # must never extend beyond the current cached trading day.
    assert first.covered_end == NOW.date()
    assert first.formal_ranking is True
    first_id = first.source_cohort_id
    rows = storage.get_source_window_coverages(
        market="sz", source_cohort_id=first_id
    )
    assert {item.source_key for item in rows if item.cohort_eligible} == {
        "cninfo_research"
    }
    supplemental = next(item for item in rows if item.source_key == "irm_ircs")
    assert supplemental.cohort_eligible is False
    assert supplemental.exclusion_reason == "历史覆盖不足，作为补充来源"

    _sync(
        storage,
        "irm_ircs",
        covered_start=days[0],
        covered_end=NOW.date(),
    )
    second = _by_market(
        build_research_source_cohorts(settings, storage, now=NOW), "sz"
    )
    assert second.source_keys == ("cninfo_research", "irm_ircs")
    assert second.supplemental_source_keys == ()
    assert second.source_cohort_id != first_id
    # Common coverage is intersection: latest start and earliest end.
    assert second.covered_start == days[0]
    assert second.covered_end == NOW.date()
    assert second.formal_ranking is True


def test_existing_cohort_source_failure_cannot_silently_shrink_cohort(tmp_path) -> None:
    storage, days = _storage_with_calendar(tmp_path)
    settings = AppSettings(app_root=tmp_path)
    for source_key in ("cninfo_research", "irm_ircs"):
        _sync(
            storage,
            source_key,
            covered_start=days[0],
            covered_end=NOW.date(),
        )
    healthy = _by_market(
        build_research_source_cohorts(settings, storage, now=NOW), "sz"
    )
    assert healthy.formal_ranking is True

    _sync(
        storage,
        "irm_ircs",
        covered_start=days[0],
        covered_end=NOW.date(),
        error="接口结构变化",
    )
    failed = _by_market(
        build_research_source_cohorts(settings, storage, now=NOW), "sz"
    )
    assert failed.source_keys == healthy.source_keys
    assert failed.source_cohort_id == healthy.source_cohort_id
    assert failed.unavailable_source_keys == ("irm_ircs",)
    assert failed.supplemental_source_keys == ()
    assert failed.formal_ranking is False


def test_institution_coverage_counts_only_activity_sources_and_cold_title(tmp_path) -> None:
    storage, days = _storage_with_calendar(tmp_path)
    settings = AppSettings(app_root=tmp_path)
    # An announcement cursor must neither increase scanned/total counts nor
    # make an institution market look covered.
    storage.save_sync_state(
        SyncCursor(
            source_key="cninfo_announcement",
            sync_kind="announcement",
            cursor={"covered_end": NOW.date().isoformat()},
            target_start=days[0],
            covered_start=days[0],
            last_success_at=NOW,
            last_error=None,
            updated_at=NOW,
        )
    )
    coverage = build_institution_research_coverage(settings, storage, now=NOW)
    assert coverage.sources_total == 4
    assert coverage.sources_scanned == 0
    assert coverage.formal_ranking is False
    assert z20_view_meta(has_formal_rows=False)[0] == "20 日机构关注（冷启动）"


def test_research_coverage_cohort_round_trip_is_stable() -> None:
    cohort = ResearchSourceCohort(
        market="sz",
        window_kind="warming_20",
        source_cohort_id="warming-v2-sz-example",
        source_keys=("cninfo_research",),
        supplemental_source_keys=("irm_ircs",),
        unavailable_source_keys=(),
        requested_start=date(2025, 8, 11),
        requested_end=NOW.date(),
        covered_start=date(2025, 8, 1),
        covered_end=NOW.date(),
        trading_days_covered=260,
        formal_ranking=True,
    )
    coverage = ResearchCoverage(
        requested_start=cohort.requested_start,
        covered_start=cohort.covered_start,
        covered_end=cohort.covered_end,
        trading_days_covered=260,
        sources_scanned=1,
        sources_total=4,
        reached_cutoff=False,
        calendar_fallback=False,
        last_success_at=NOW,
        provisional=True,
        error=None,
        market_cohorts=(cohort,),
        formal_ranking=False,
    )
    assert ResearchCoverage.from_dict(coverage.to_dict()) == coverage

"""Market-specific comparable coverage for institution warming metrics.

Only public ``research_activity`` feeds participate here.  Announcement
backfill continues to serve the event boards, but can neither improve nor
degrade an institution cohort.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import date, datetime, timedelta

from .config import AppSettings, SHANGHAI_TZ, SourceConfig
from .models import ResearchCoverage, ResearchSourceCohort, SourceWindowCoverage
from .storage import Storage
from .trading_calendar import TradingCalendarService


WARMING_WINDOW_KIND = "warming_20"
WARMING_REQUIRED_TRADING_DAYS = 260  # current 20 + twelve prior 20-day buckets
WARMING_MIN_TRADING_DAYS = 120  # current 20 + five prior buckets (provisional)

# Source applicability is deliberately explicit.  ``cninfo_research`` uses
# the SZSE relation column; Shanghai and Beijing each have their own official
# activity stream.  Announcement sources are intentionally absent.
MARKET_RESEARCH_SOURCE_KEYS: dict[str, tuple[str, ...]] = {
    "sh": ("sse_publish",),
    "sz": ("cninfo_research", "irm_ircs"),
    "bj": ("bse_performance",),
}


def stock_market(stock_code: str) -> str | None:
    """Return the institution-source market for a six-digit A-share code."""

    code = str(stock_code).strip()
    if len(code) != 6 or not code.isdigit():
        return None
    if code.startswith(("4", "8", "92")):
        return "bj"
    if code.startswith(("5", "6", "9")):
        return "sh"
    if code.startswith(("0", "1", "2", "3")):
        return "sz"
    return None


def applicable_research_sources(
    settings: AppSettings, market: str
) -> tuple[SourceConfig, ...]:
    """Configured research-activity sources applicable to one market."""

    keys = set(MARKET_RESEARCH_SOURCE_KEYS.get(market, ()))
    return tuple(
        config
        for config in settings.research_sources
        if config.kind == "research_activity" and config.key in keys
    )


@dataclass(frozen=True, slots=True)
class _SourceStatus:
    config: SourceConfig
    covered_start: date | None
    covered_end: date | None
    reached_cutoff: bool
    reconciled: bool
    stable: bool
    eligible: bool
    last_success_at: datetime | None
    error: str | None
    exclusion_reason: str | None


def _parse_covered_end(value: object) -> date | None:
    if not value:
        return None
    try:
        return date.fromisoformat(str(value))
    except ValueError:
        return None


def _source_status(
    storage: Storage,
    config: SourceConfig,
    *,
    requested_start: date,
    stable_start: date,
    requested_end: date,
) -> _SourceStatus:
    state = storage.get_sync_state(config.key, "research_activity")
    if state is None:
        return _SourceStatus(
            config, None, None, False, False, False, False, None, None, "尚未同步"
        )

    success = state.last_success_at is not None and state.last_error is None
    raw_end = _parse_covered_end(
        state.cursor.get("covered_end") if state.cursor else None
    )
    # A successful newest-first scan covers the list through its run date even
    # when that day contains no activity document.  Never let adapter dates
    # extend beyond the last cached trading day used by the metric.
    observed_end = raw_end
    if success and state.last_success_at is not None:
        scan_day = state.last_success_at.astimezone(SHANGHAI_TZ).date()
        observed_end = max(
            (item for item in (raw_end, scan_day) if item is not None),
            default=None,
        )
    covered_end = min(observed_end, requested_end) if observed_end else None
    reached_cutoff = bool(
        state.covered_start is not None
        and state.covered_start <= requested_start
    )
    reconciled = bool(
        success
        and reached_cutoff
        and covered_end is not None
        and covered_end >= requested_end
    )
    stable = bool(
        success
        and state.covered_start is not None
        and state.covered_start <= stable_start
        and covered_end is not None
        and covered_end >= requested_end
    )
    eligible = reconciled
    if state.last_error:
        exclusion_reason = "存在未关闭错误"
    elif state.last_success_at is None:
        exclusion_reason = "尚无成功同步"
    elif not stable:
        exclusion_reason = "历史覆盖不足，作为补充来源"
    elif not reached_cutoff:
        exclusion_reason = "已进入 cohort，但仅生成暂定值"
    elif covered_end is None or covered_end < requested_end:
        exclusion_reason = "最新覆盖不足，作为补充来源"
    else:
        exclusion_reason = None
    return _SourceStatus(
        config=config,
        covered_start=state.covered_start,
        covered_end=covered_end,
        reached_cutoff=reached_cutoff,
        reconciled=reconciled,
        stable=stable,
        eligible=eligible,
        last_success_at=state.last_success_at,
        error=state.last_error,
        exclusion_reason=exclusion_reason,
    )


def _cohort_id(market: str, source_keys: tuple[str, ...]) -> str:
    if not source_keys:
        return f"warming-v2-{market}-cold"
    digest = hashlib.sha256("\n".join(source_keys).encode("utf-8")).hexdigest()[:12]
    return f"warming-v2-{market}-{digest}"


def build_research_source_cohorts(
    settings: AppSettings,
    storage: Storage,
    *,
    calendar: TradingCalendarService | None = None,
    now: datetime | None = None,
    window_kind: str = WARMING_WINDOW_KIND,
    required_trading_days: int = WARMING_REQUIRED_TRADING_DAYS,
) -> tuple[ResearchSourceCohort, ...]:
    """Build and persist sticky, market-specific source cohorts.

    A newly configured source is supplemental until it covers the whole
    comparison window.  Once eligible, it becomes a sticky cohort member; a
    later failure makes the market non-formal instead of changing the metric's
    source universe underneath an existing baseline.
    """

    now_dt = now or datetime.now(SHANGHAI_TZ)
    calendar_service = calendar or TradingCalendarService(storage)
    trading_days = calendar_service.last_n_trading_days(
        now_dt.date(), required_trading_days
    )
    requested_end = trading_days[-1] if trading_days else now_dt.date()
    requested_start = (
        trading_days[0]
        if trading_days
        else requested_end - timedelta(days=settings.backfill_days)
    )
    stable_start = (
        trading_days[-WARMING_MIN_TRADING_DAYS]
        if len(trading_days) >= WARMING_MIN_TRADING_DAYS
        else requested_start
    )
    calendar_complete = len(trading_days) >= required_trading_days
    cohorts: list[ResearchSourceCohort] = []

    for market in ("sh", "sz", "bj"):
        configs = applicable_research_sources(settings, market)
        statuses = {
            config.key: _source_status(
                storage,
                config,
                requested_start=requested_start,
                stable_start=stable_start,
                requested_end=requested_end,
            )
            for config in configs
        }
        applicable_keys = tuple(config.key for config in configs)
        previous_members = {
            item.source_key
            for item in storage.get_source_window_coverages(
                market=market, window_kind=window_kind
            )
            if item.cohort_eligible and item.source_key in statuses
        }
        currently_stable = {
            key for key, status in statuses.items() if status.stable
        }
        # Sticky previous membership prevents source failures from producing a
        # deceptively healthier but incomparable smaller cohort.
        source_keys = tuple(
            key
            for key in applicable_keys
            if key in previous_members or key in currently_stable
        )
        source_cohort_id = _cohort_id(market, source_keys)
        supplemental = tuple(
            key for key in applicable_keys if key not in source_keys
        )
        unavailable = tuple(
            key
            for key in source_keys
            if not statuses[key].stable
        )
        healthy = [statuses[key] for key in source_keys if statuses[key].stable]
        covered_start = max(
            (item.covered_start for item in healthy if item.covered_start),
            default=None,
        )
        covered_end = min(
            (item.covered_end for item in healthy if item.covered_end),
            default=None,
        )
        trading_days_covered = (
            calendar_service.trading_day_count_between(covered_start, covered_end)
            if covered_start is not None and covered_end is not None
            else 0
        )
        formal_ranking = bool(
            calendar_complete
            and source_keys
            and not unavailable
            and all(statuses[key].eligible for key in source_keys)
            and covered_start is not None
            and covered_start <= requested_start
            and covered_end is not None
            and covered_end >= requested_end
        )

        for key in applicable_keys:
            status = statuses[key]
            storage.upsert_source_window_coverage(
                SourceWindowCoverage(
                    source_key=key,
                    market=market,
                    source_kind="research_activity",
                    window_kind=window_kind,
                    source_cohort_id=source_cohort_id,
                    requested_start=requested_start,
                    requested_end=requested_end,
                    covered_start=status.covered_start,
                    covered_end=status.covered_end,
                    reached_cutoff=status.reached_cutoff,
                    reconciled=status.reconciled,
                    # The persisted eligibility flag denotes *complete* 12-bucket
                    # comparability.  Stable 5-11 bucket members stay in the
                    # cohort summary but are explicitly provisional.
                    cohort_eligible=key in source_keys and status.eligible,
                    last_success_at=status.last_success_at,
                    error=status.error,
                    exclusion_reason=status.exclusion_reason,
                    updated_at=now_dt,
                )
            )

        cohorts.append(
            ResearchSourceCohort(
                market=market,
                window_kind=window_kind,
                source_cohort_id=source_cohort_id,
                source_keys=source_keys,
                supplemental_source_keys=supplemental,
                unavailable_source_keys=unavailable,
                requested_start=requested_start,
                requested_end=requested_end,
                covered_start=covered_start,
                covered_end=covered_end,
                trading_days_covered=trading_days_covered,
                formal_ranking=formal_ranking,
            )
        )
    return tuple(cohorts)


def build_institution_research_coverage(
    settings: AppSettings,
    storage: Storage,
    *,
    calendar: TradingCalendarService | None = None,
    now: datetime | None = None,
) -> ResearchCoverage:
    """Aggregate market cohorts for institution-board display and gating."""

    now_dt = now or datetime.now(SHANGHAI_TZ)
    calendar_service = calendar or TradingCalendarService(storage)
    cohorts = build_research_source_cohorts(
        settings, storage, calendar=calendar_service, now=now_dt
    )
    configs = tuple(
        config
        for config in settings.research_sources
        if config.kind == "research_activity"
        and any(
            config.key in keys for keys in MARKET_RESEARCH_SOURCE_KEYS.values()
        )
    )
    states = [
        storage.get_sync_state(config.key, "research_activity")
        for config in configs
    ]
    successful = [
        state
        for state in states
        if state is not None
        and state.last_success_at is not None
        and state.last_error is None
    ]
    cohort_starts = [
        item.covered_start for item in cohorts if item.covered_start is not None
    ]
    cohort_ends = [
        item.covered_end for item in cohorts if item.covered_end is not None
    ]
    covered_start = max(cohort_starts, default=None)
    covered_end = min(cohort_ends, default=None)
    requested_start = min(item.requested_start for item in cohorts)
    requested_end = min(item.requested_end for item in cohorts)
    calendar_states = [
        calendar_service.get_calendar_state(year)
        for year in range(requested_start.year, requested_end.year + 1)
    ]
    calendar_fallback = any(item.calendar_fallback for item in calendar_states)
    calendar_missing = any(item.source is None for item in calendar_states)
    source_errors = [
        state.last_error for state in states if state is not None and state.last_error
    ]
    calendar_errors = [item.last_error for item in calendar_states if item.last_error]
    all_formal = bool(cohorts) and all(item.formal_ranking for item in cohorts)
    return ResearchCoverage(
        requested_start=requested_start,
        covered_start=covered_start,
        covered_end=covered_end,
        trading_days_covered=(
            calendar_service.trading_day_count_between(covered_start, covered_end)
            if covered_start is not None and covered_end is not None
            else 0
        ),
        sources_scanned=len(successful),
        sources_total=len(configs),
        reached_cutoff=all_formal,
        calendar_fallback=calendar_fallback,
        last_success_at=max(
            (state.last_success_at for state in successful), default=None
        ),
        provisional=calendar_fallback or calendar_missing or not all_formal,
        error=(source_errors + calendar_errors)[0]
        if source_errors or calendar_errors
        else None,
        market_cohorts=cohorts,
        formal_ranking=all_formal,
    )


def cohort_for_stock(
    coverage: ResearchCoverage | None, stock_code: str
) -> ResearchSourceCohort | None:
    """Return the persisted market cohort applicable to a stock row."""

    if coverage is None:
        return None
    market = stock_market(stock_code)
    return next(
        (item for item in coverage.market_cohorts if item.market == market), None
    )

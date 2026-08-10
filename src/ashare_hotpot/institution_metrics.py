"""20/60/120-trading-day institution metrics (plan.md section 13).

The pipeline is: research documents -> activity parsing (persisted) ->
calendar-bucketed metrics -> transparent boards.  Everything here consumes
persisted structured models only; it never fetches web pages and never infers
investment opinions.  Group-level counting (plan.md 12.2) means reposts of
the same collective research never amplify breadth, and single-day mega
activities stay constrained by the concentration component.
"""

from __future__ import annotations

import logging
from collections import Counter, defaultdict
from dataclasses import dataclass, field, replace
from datetime import date, datetime, timedelta

from .config import AppSettings, SHANGHAI_TZ
from .institution_coverage import (
    build_institution_research_coverage,
    cohort_for_stock,
    stock_market,
)
from .institutions import InstitutionRegistry
from .models import (
    PersistenceRow,
    ReportedParticipantCount,
    ResearchCoverage,
    StructuralComparison,
    WarmingV2Row,
    Z20Row,
)
from .research_activities import (
    DEPTH_WEIGHTS,
    RESEARCH_INSTITUTION_TYPES,
    parse_research_activity,
)
from .storage import Storage
from .trading_calendar import TradingCalendarService


logger = logging.getLogger(__name__)


def _week_key(day: date) -> tuple[int, int]:
    iso = day.isocalendar()
    return (iso.year, iso.week)


def _date_ts(day: date | None) -> int:
    return day.toordinal() if day is not None else 0


def _dt_at(day: date) -> datetime:
    return datetime(day.year, day.month, day.day, tzinfo=SHANGHAI_TZ)


def trading_buckets(
    trading_days: list[date], bucket_size: int = 20
) -> list[tuple[date, date]]:
    """Consecutive non-overlapping buckets from the end, ascending.

    With 120 trading days this returns six 20-day buckets; the last one is the
    current bucket and the five preceding ones form the baseline.
    """

    buckets: list[tuple[date, date]] = []
    for index in range(len(trading_days) - 1, -1, -bucket_size):
        chunk = trading_days[max(0, index - bucket_size + 1) : index + 1]
        buckets.append((chunk[0], chunk[-1]))
    buckets.reverse()
    return buckets


def z20_from_counts(current: int, previous: list[int]) -> float:
    """plan.md 13.2: ``(current - mean(prev)) / max(std(prev), 1)``.

    A zero standard deviation (including empty baselines) falls back to
    dividing by 1, so the raw difference remains visible.
    """

    mean = sum(previous) / len(previous) if previous else 0.0
    if len(previous) < 2:
        std = 0.0
    else:
        variance = sum((value - mean) ** 2 for value in previous) / len(previous)
        std = variance**0.5
    return round((current - mean) / max(std, 1.0), 3)


@dataclass(frozen=True, slots=True)
class WarmingStatistics:
    """Transparent components of the descriptive ``warming_v2`` value."""

    score: float | None
    baseline_mean: float | None
    baseline_variance: float | None
    predictive_variance: float | None
    absolute_change: float | None
    baseline_bucket_count: int


def warming_v2_statistics(
    current: int, previous: list[int]
) -> WarmingStatistics:
    """Return the fixed warming-v2 formula for 5-12 historical buckets.

    The variance is the sample variance (``ddof=1``).  Fewer than five
    complete historical buckets intentionally return no standardized value.
    """

    n = len(previous)
    if n < 5:
        return WarmingStatistics(None, None, None, None, None, n)
    mean = sum(previous) / n
    variance = sum((value - mean) ** 2 for value in previous) / (n - 1)
    predictive_variance = max(mean, variance, 1.0) * (1.0 + 1.0 / n)
    return WarmingStatistics(
        score=round((current - mean) / predictive_variance**0.5, 3),
        baseline_mean=round(mean, 4),
        baseline_variance=round(variance, 4),
        predictive_variance=round(predictive_variance, 4),
        absolute_change=round(current - mean, 4),
        baseline_bucket_count=n,
    )


@dataclass(frozen=True, slots=True)
class _ActivityData:
    activity_id: str
    stock_code: str
    activity_dates: tuple[date, ...]
    end_date: date
    groups: frozenset[str]
    analysts: frozenset[str]
    question_count: int
    high_depth_question_count: int
    depth_counts: dict[str, int]
    topics: dict[str, int]
    type_counts: dict[str, int]
    group_dates: dict[str, frozenset[date]] = field(default_factory=dict)
    source_key: str | None = None
    question_data_status: str = "available"  # available | missing_body
    date_mapping_complete: bool = True
    date_quality: str = "explicit_day"
    excluded_organization_count: int = 0


def _load_activity_data(
    storage: Storage,
    start: date,
    end: date,
    *,
    allowed_sources_by_market: dict[str, frozenset[str]] | None = None,
) -> list[_ActivityData]:
    """Load persisted activities with group/analyst/type aggregation.

    Warming-v2 occurrence rows take precedence when present: only reliable
    explicit days and ``research_eligible`` institution/date mappings enter
    the metric input.  Activities not yet recomputed retain the legacy read
    path for the one-version compatibility period.
    """

    result: list[_ActivityData] = []
    source_by_document = {
        item.document_id: item.source_key
        for item in storage.get_discovery_candidates()
        if item.kind == "research_activity"
    }
    for activity in storage.get_research_activities_between(start, end):
        document = storage.get_source_document(activity.source_document_id)
        source_key = source_by_document.get(activity.source_document_id)
        if source_key is None:
            provider = document.provider_key if document is not None else ""
            market = stock_market(activity.stock_code)
            source_key = {
                ("sh", "sse"): "sse_publish",
                ("sz", "cninfo"): "cninfo_research",
                ("sz", "irm"): "irm_ircs",
                ("bj", "bse"): "bse_performance",
            }.get((market, provider))
        market = stock_market(activity.stock_code)
        if allowed_sources_by_market is not None and (
            market is None
            or source_key not in allowed_sources_by_market.get(market, frozenset())
        ):
            continue
        groups: set[str] = set()
        analysts: set[str] = set()
        type_counts: Counter[str] = Counter()
        group_dates: dict[str, set[date]] = defaultdict(set)
        occurrence_rows = storage.get_activity_occurrences(activity.activity_id)
        date_mapping_complete = bool(occurrence_rows)
        excluded_organization_count = 0
        if occurrence_rows:
            eligible_occurrences = {
                item.occurrence_id: item
                for item in occurrence_rows
                if item.metric_eligible and item.occurred_on is not None
            }
            if not eligible_occurrences:
                continue
            activity_dates = tuple(
                sorted({item.occurred_on for item in eligible_occurrences.values()})
            )
            counted_institutions: set[str] = set()
            participant_occurrences = (
                storage.get_research_participant_occurrences(activity.activity_id)
            )
            excluded_organization_count = sum(
                1 for item in participant_occurrences if not item.research_eligible
            )
            for participant in participant_occurrences:
                if not participant.research_eligible:
                    continue
                occurrence = eligible_occurrences.get(
                    participant.activity_occurrence_id
                )
                if occurrence is None or occurrence.occurred_on is None:
                    continue
                institution = storage.get_institution(participant.institution_id)
                if institution is None:
                    continue
                group = institution.group_id or institution.institution_id
                groups.add(group)
                group_dates[group].add(occurrence.occurred_on)
                if participant.analyst_name:
                    analysts.add(
                        f"{participant.institution_id}\x00{participant.analyst_name}"
                    )
                if participant.institution_id not in counted_institutions:
                    counted_institutions.add(participant.institution_id)
                    type_counts[institution.institution_type] += 1
        else:
            date_mapping_complete = False
            if not activity.activity_dates:
                continue
            activity_dates = activity.activity_dates
            for participant in storage.get_research_participants(activity.activity_id):
                institution = storage.get_institution(participant.institution_id)
                if institution is None:
                    continue
                group = institution.group_id or institution.institution_id
                groups.add(group)
                group_dates[group].add(max(activity_dates))
                if participant.analyst_name:
                    analysts.add(
                        f"{participant.institution_id}\x00{participant.analyst_name}"
                    )
                type_counts[institution.institution_type] += 1
        result.append(
            _ActivityData(
                activity_id=activity.activity_id,
                stock_code=activity.stock_code,
                activity_dates=activity_dates,
                end_date=max(activity_dates),
                groups=frozenset(groups),
                analysts=frozenset(analysts),
                question_count=activity.question_count,
                high_depth_question_count=activity.high_depth_question_count,
                depth_counts=dict(activity.depth_counts or {}),
                topics=dict(activity.topic_counts or {}),
                type_counts=dict(type_counts),
                group_dates={
                    group: frozenset(days)
                    for group, days in group_dates.items()
                },
                source_key=source_key,
                question_data_status=(
                    "available" if activity.question_count > 0 else "missing_body"
                ),
                date_mapping_complete=(
                    date_mapping_complete
                    and bool(groups)
                    and all(group_dates.get(group) for group in groups)
                ),
                date_quality=("explicit_day" if occurrence_rows else "legacy_unknown"),
                excluded_organization_count=excluded_organization_count,
            )
        )
    return result


def persistence_components(
    activities: list[_ActivityData],
    window_trading_days: list[date],
) -> tuple[int, float, float, float, float, int, date | None]:
    """plan.md 13.3 components for one stock window.

    Returns ``(active_weeks, active_week_ratio, repeat_followup_ratio,
    depth_score, single_day_concentration, unique_groups, recent_activity)``.
    """

    groups: set[str] = set()
    group_dates: dict[str, set[date]] = defaultdict(set)
    day_groups: dict[date, set[str]] = defaultdict(set)
    active_weeks: set[tuple[int, int]] = set()
    window_days = set(window_trading_days)
    total_low = total_medium = total_high = total_questions = 0
    recent: date | None = None
    for activity in activities:
        groups.update(activity.groups)
        for day in activity.activity_dates:
            if day in window_days:
                active_weeks.add(_week_key(day))
        for group in activity.groups:
            dates = activity.group_dates.get(group) or frozenset(
                (activity.end_date,)
            )
            for mapped_day in dates:
                if mapped_day not in window_days:
                    continue
                group_dates[group].add(mapped_day)
                day_groups[mapped_day].add(group)
        total_low += activity.depth_counts.get("low", 0)
        total_medium += activity.depth_counts.get("medium", 0)
        total_high += activity.depth_counts.get("high", 0)
        total_questions += activity.question_count
        if recent is None or activity.end_date > recent:
            recent = activity.end_date

    total_weeks = {_week_key(day) for day in window_trading_days}
    active_week_ratio = len(active_weeks) / len(total_weeks) if total_weeks else 0.0
    repeat_count = sum(1 for days in group_dates.values() if len(days) >= 2)
    repeat_followup_ratio = repeat_count / len(groups) if groups else 0.0
    depth_score = (
        (
            DEPTH_WEIGHTS["low"] * total_low
            + DEPTH_WEIGHTS["medium"] * total_medium
            + DEPTH_WEIGHTS["high"] * total_high
        )
        / total_questions
        if total_questions
        else 0.0
    )
    total_pairs = sum(len(days) for days in day_groups.values())
    max_daily = max((len(day_set) for day_set in day_groups.values()), default=0)
    concentration = max_daily / total_pairs if total_pairs else 0.0
    return (
        len(active_weeks),
        active_week_ratio,
        repeat_followup_ratio,
        depth_score,
        concentration,
        len(groups),
        recent,
    )


@dataclass(frozen=True, slots=True)
class PersistenceRuleComponents:
    """Four visible rule components plus their data-availability contract."""

    active_weeks: int
    active_week_ratio: float
    unique_groups: int
    repeat_followup_ratio: float
    depth_score: float | None
    single_day_concentration: float
    recent_activity: date | None
    question_data_status: str  # available | missing_body | partial_missing
    date_mapping_complete: bool


def persistence_rule_components(
    activities: list[_ActivityData],
    window_trading_days: list[date],
) -> PersistenceRuleComponents:
    """Compute v2 components without treating missing evidence as zero."""

    (
        active_weeks,
        active_week_ratio,
        repeat_followup_ratio,
        _legacy_depth,
        concentration,
        unique_groups,
        recent,
    ) = persistence_components(activities, window_trading_days)
    statuses = {item.question_data_status for item in activities}
    if not activities or statuses == {"missing_body"}:
        question_status = "missing_body"
    elif "missing_body" in statuses:
        question_status = "partial_missing"
    else:
        question_status = "available"
    total_questions = sum(item.question_count for item in activities)
    if question_status != "available" or total_questions <= 0:
        depth_score: float | None = None
    else:
        total_low = sum(item.depth_counts.get("low", 0) for item in activities)
        total_medium = sum(
            item.depth_counts.get("medium", 0) for item in activities
        )
        total_high = sum(item.depth_counts.get("high", 0) for item in activities)
        depth_score = (
            DEPTH_WEIGHTS["low"] * total_low
            + DEPTH_WEIGHTS["medium"] * total_medium
            + DEPTH_WEIGHTS["high"] * total_high
        ) / total_questions
    date_mapping_complete = bool(
        activities
        and unique_groups > 0
        and all(
            item.date_mapping_complete
            and all(item.group_dates.get(group) for group in item.groups)
            for item in activities
        )
    )
    return PersistenceRuleComponents(
        active_weeks=active_weeks,
        active_week_ratio=round(active_week_ratio, 4),
        unique_groups=unique_groups,
        repeat_followup_ratio=round(repeat_followup_ratio, 4),
        depth_score=round(depth_score, 4) if depth_score is not None else None,
        single_day_concentration=round(concentration, 4),
        recent_activity=recent,
        question_data_status=question_status,
        date_mapping_complete=date_mapping_complete,
    )


def persistence_rule_index(components: PersistenceRuleComponents) -> float | None:
    """Return the descriptive rule index, or ``None`` when evidence is absent."""

    if (
        components.unique_groups <= 0
        or not components.date_mapping_complete
        or components.question_data_status != "available"
        or components.depth_score is None
    ):
        return None
    return persistence_score(
        components.active_week_ratio,
        components.repeat_followup_ratio,
        components.depth_score,
        components.single_day_concentration,
    )


def persistence_score(
    active_week_ratio: float,
    repeat_followup_ratio: float,
    depth_score: float,
    concentration: float,
) -> float:
    """plan.md 13.3 weighted persistence score on a 0-100 scale."""

    return round(
        100.0
        * (
            0.40 * active_week_ratio
            + 0.25 * repeat_followup_ratio
            + 0.20 * depth_score
            + 0.15 * (1.0 - concentration)
        ),
        2,
    )


def industry_percentiles(
    current_groups_by_code: dict[str, int],
    industries: dict[str, str],
) -> tuple[dict[str, float | None], dict[str, int]]:
    """plan.md 13.2: industry percentile of current group breadth.

    Industries with fewer than 5 sample stocks return ``None`` (degradation)
    instead of a misleading percentile.
    """

    by_industry: dict[str, dict[str, int]] = defaultdict(dict)
    for code, value in current_groups_by_code.items():
        industry = industries.get(code)
        if not industry:
            continue
        by_industry[industry][code] = value
    percentiles: dict[str, float | None] = {}
    sample_sizes: dict[str, int] = {}
    for industry, members in by_industry.items():
        values = list(members.values())
        size = len(values)
        for code in members:
            sample_sizes[code] = size
        if size < 5:
            for code in members:
                percentiles[code] = None
            continue
        for code, value in members.items():
            lower = sum(1 for other in values if other < value)
            equal = sum(1 for other in values if other == value)
            percentiles[code] = round((lower + 0.5 * equal) / size * 100.0, 1)
    return percentiles, sample_sizes


def sort_z20(rows: list[Z20Row]) -> list[Z20Row]:
    """plan.md 13.2 ordering; cold-start rows (z20 None) sort by raw metrics."""

    return sorted(
        rows,
        key=lambda row: (
            (
                0,
                -row.z20,
                -row.new_groups,
                -row.high_depth_ratio,
                _date_ts(row.recent_activity),
                row.stock_code,
            )
            if row.z20 is not None
            else (
                1,
                -row.current_unique_groups,
                -row.new_groups,
                _date_ts(row.recent_activity),
                row.stock_code,
            )
        ),
    )


def _complete_trading_buckets(
    trading_days: list[date], bucket_size: int = 20
) -> list[tuple[date, date]]:
    complete_count = len(trading_days) // bucket_size
    if complete_count <= 0:
        return []
    complete_days = trading_days[-complete_count * bucket_size :]
    return [
        (complete_days[index], complete_days[index + bucket_size - 1])
        for index in range(0, len(complete_days), bucket_size)
    ]


def _groups_in_period(
    activities: list[_ActivityData], start: date, end: date
) -> set[str]:
    groups: set[str] = set()
    for activity in activities:
        for group in activity.groups:
            mapped_days = activity.group_dates.get(group) or frozenset(
                (activity.end_date,)
            )
            if any(start <= day <= end for day in mapped_days):
                groups.add(group)
    return groups


def _current_warming_components(
    activities: list[_ActivityData], start: date, end: date
) -> tuple[set[str], set[date], float, date | None]:
    groups = _groups_in_period(activities, start, end)
    groups_by_day: dict[date, set[str]] = defaultdict(set)
    recent: date | None = None
    for activity in activities:
        for group in activity.groups:
            mapped_days = activity.group_dates.get(group) or frozenset(
                (activity.end_date,)
            )
            for day in mapped_days:
                if start <= day <= end:
                    groups_by_day[day].add(group)
                    recent = day if recent is None or day > recent else recent
    total_group_days = sum(len(items) for items in groups_by_day.values())
    maximum_day = max((len(items) for items in groups_by_day.values()), default=0)
    concentration = (
        maximum_day / total_group_days if total_group_days else 0.0
    )
    return groups, set(groups_by_day), round(concentration, 4), recent


def _warming_industry_percentiles(
    scores: dict[str, float],
    industries: dict[str, str],
) -> tuple[dict[str, float], dict[str, int]]:
    by_industry: dict[str, dict[str, float]] = defaultdict(dict)
    for code, industry in industries.items():
        if code in scores and industry:
            by_industry[industry][code] = scores[code]
    percentiles: dict[str, float] = {}
    sizes: dict[str, int] = {}
    for members in by_industry.values():
        size = len(members)
        for code in members:
            sizes[code] = size
        if size < 20:
            continue
        values = list(members.values())
        for code, value in members.items():
            lower = sum(1 for other in values if other < value)
            equal = sum(1 for other in values if other == value)
            percentiles[code] = round(
                (lower + 0.5 * equal) / size * 100.0, 1
            )
    return percentiles, sizes


def warming_v2_rows(
    activities: list[_ActivityData],
    trading_days: list[date],
    coverage: ResearchCoverage,
    industries: dict[str, str],
    *,
    complete_industry_universe: dict[str, str] | None = None,
) -> list[WarmingV2Row]:
    """Build descriptive warming rows from occurrence-qualified activities.

    ``complete_industry_universe`` must contain every listed company, including
    zero-activity stocks.  Omitting it deliberately disables industry
    percentiles; the ordinary activity subset is never treated as a universe.
    """

    buckets = _complete_trading_buckets(trading_days)
    if not buckets:
        return []
    current_start, current_end = buckets[-1]
    historical = buckets[:-1][-12:]
    by_stock: dict[str, list[_ActivityData]] = defaultdict(list)
    for activity in activities:
        by_stock[activity.stock_code].append(activity)

    def components_for(code: str) -> tuple[
        WarmingStatistics,
        str,
        int | None,
        int,
        int,
        float,
        date | None,
        str,
        str,
        int,
    ] | None:
        cohort = cohort_for_stock(coverage, code)
        if cohort is None or not cohort.source_keys:
            return None
        acts = by_stock.get(code, [])
        current_groups, active_dates, concentration, recent = (
            _current_warming_components(acts, current_start, current_end)
        )
        current_activities = [
            item
            for item in acts
            if any(current_start <= day <= current_end for day in item.activity_dates)
        ]
        date_qualities = {item.date_quality for item in current_activities}
        date_quality = (
            next(iter(date_qualities))
            if len(date_qualities) == 1
            else ("mixed" if date_qualities else "unknown")
        )
        excluded_count = sum(
            item.excluded_organization_count for item in current_activities
        )
        available_history = [
            (start, end)
            for start, end in historical
            if cohort.covered_start is not None
            and cohort.covered_end is not None
            and cohort.covered_start <= start
            and cohort.covered_end >= end
        ]
        previous_counts = [
            len(_groups_in_period(acts, start, end))
            for start, end in available_history
        ]
        stats = warming_v2_statistics(len(current_groups), previous_counts)
        comparable = not cohort.unavailable_source_keys
        if (
            comparable
            and cohort.formal_ranking
            and len(previous_counts) == 12
        ):
            level = "full"
        elif comparable and len(previous_counts) >= 5:
            level = "provisional"
        else:
            level = "raw_only"
            stats = WarmingStatistics(
                None,
                stats.baseline_mean if len(previous_counts) >= 5 else None,
                stats.baseline_variance if len(previous_counts) >= 5 else None,
                stats.predictive_variance if len(previous_counts) >= 5 else None,
                stats.absolute_change if len(previous_counts) >= 5 else None,
                len(previous_counts),
            )
        prior_100 = available_history[-5:]
        unseen = (
            len(
                current_groups
                - {
                    group
                    for start, end in prior_100
                    for group in _groups_in_period(acts, start, end)
                }
            )
            if len(prior_100) == 5
            else None
        )
        return (
            stats,
            level,
            unseen,
            len(current_groups),
            len(active_dates),
            concentration,
            recent,
            cohort.source_cohort_id,
            date_quality,
            excluded_count,
        )

    row_components = {
        code: value
        for code in sorted(by_stock)
        if (value := components_for(code)) is not None and value[3] > 0
    }
    percentiles: dict[str, float] = {}
    sample_sizes: dict[str, int] = {}
    if complete_industry_universe is not None:
        universe_scores: dict[str, float] = {}
        for code in complete_industry_universe:
            value = components_for(code)
            if value is not None and value[1] == "full" and value[0].score is not None:
                universe_scores[code] = value[0].score
        percentiles, sample_sizes = _warming_industry_percentiles(
            universe_scores, complete_industry_universe
        )

    rows: list[WarmingV2Row] = []
    for code, value in row_components.items():
        (
            stats,
            level,
            unseen,
            current_groups,
            active_days,
            concentration,
            recent,
            cohort_id,
            date_quality,
            excluded_count,
        ) = value
        if level == "full":
            provisional_reason = None
        elif level == "provisional":
            provisional_reason = f"历史基线仅 {stats.baseline_bucket_count} 个完整桶"
        else:
            provisional_reason = (
                "cohort 来源当前不可用"
                if cohort_for_stock(coverage, code)
                and cohort_for_stock(coverage, code).unavailable_source_keys
                else "历史基线不足 5 个完整桶"
            )
        rows.append(
            WarmingV2Row(
                stock_code=code,
                industry=industries.get(code),
                warming_score=stats.score,
                baseline_mean=stats.baseline_mean,
                baseline_variance=stats.baseline_variance,
                predictive_variance=stats.predictive_variance,
                baseline_bucket_count=stats.baseline_bucket_count,
                coverage_level=level,
                absolute_change=stats.absolute_change,
                current_unique_groups=current_groups,
                unseen_100d_groups=unseen,
                active_days=active_days,
                single_day_concentration=concentration,
                single_day=active_days == 1,
                recent_activity=recent,
                industry_percentile=percentiles.get(code),
                industry_sample_size=sample_sizes.get(code, 0),
                source_cohort_id=cohort_id,
                date_quality=date_quality,
                excluded_organization_count=excluded_count,
                provisional_reason=provisional_reason,
            )
        )
    return sort_warming_v2(rows)


def sort_warming_v2(rows: list[WarmingV2Row]) -> list[WarmingV2Row]:
    """Fixed v2 order with deterministic stock-code fallback."""

    level_rank = {"full": 0, "provisional": 1, "raw_only": 2}
    return sorted(
        rows,
        key=lambda row: (
            level_rank.get(row.coverage_level, 3),
            -(row.warming_score if row.warming_score is not None else float("-inf")),
            -(row.unseen_100d_groups if row.unseen_100d_groups is not None else -1),
            -row.active_days,
            -row.current_unique_groups,
            -_date_ts(row.recent_activity),
            row.stock_code,
        ),
    )


def sort_persistence(rows: list[PersistenceRow]) -> list[PersistenceRow]:
    """plan.md 13.3 ordering: score -> active weeks -> groups -> recency -> code."""

    return sorted(
        rows,
        key=lambda row: (
            0 if row.persistence_score is not None else 1,
            -(row.persistence_score if row.persistence_score is not None else 0.0),
            -row.active_weeks,
            -row.unique_groups,
            _date_ts(row.recent_activity),
            row.stock_code,
        ),
    )


def _type_shares(activities: list[_ActivityData]) -> dict[str, float]:
    counter: Counter[str] = Counter()
    total = 0
    for activity in activities:
        for institution_type, count in activity.type_counts.items():
            counter[institution_type] += count
            total += count
    return {kind: count / total for kind, count in counter.items()} if total else {}


def _high_depth_ratio(activities: list[_ActivityData]) -> float | None:
    questions = sum(activity.question_count for activity in activities)
    if not questions:
        return None
    high = sum(activity.high_depth_question_count for activity in activities)
    return high / questions


def _week_ratio(
    activities: list[_ActivityData], window_trading_days: list[date]
) -> float:
    window_days = set(window_trading_days)
    total_weeks = {_week_key(day) for day in window_trading_days}
    if not total_weeks:
        return 0.0
    active_weeks = {
        _week_key(day)
        for activity in activities
        for day in activity.activity_dates
        if day in window_days
    }
    return len(active_weeks) / len(total_weeks)


def _concentration(activities: list[_ActivityData]) -> float:
    day_groups: dict[date, set[str]] = defaultdict(set)
    for activity in activities:
        for group in activity.groups:
            day_groups[activity.end_date].add(group)
    total_pairs = sum(len(day_set) for day_set in day_groups.values())
    max_daily = max((len(day_set) for day_set in day_groups.values()), default=0)
    return max_daily / total_pairs if total_pairs else 0.0


def structural_comparison(
    stock_code: str,
    activities: list[_ActivityData],
    prior_days: list[date],
    recent_days: list[date],
) -> StructuralComparison:
    """plan.md 13.3: recent-60 vs prior-60 research-behavior structure."""

    def _in_window(days: list[date]) -> list[_ActivityData]:
        start, end = days[0], days[-1]
        return [activity for activity in activities if start <= activity.end_date <= end]

    prior = _in_window(prior_days)
    recent = _in_window(recent_days)

    def _groups(items: list[_ActivityData]) -> set[str]:
        result: set[str] = set()
        for item in items:
            result.update(item.groups)
        return result

    prior_groups = _groups(prior)
    recent_groups = _groups(recent)
    prior_shares = _type_shares(prior)
    recent_shares = _type_shares(recent)
    types = sorted(set(prior_shares) | set(recent_shares))

    def _delta(before: float | None, after: float | None) -> float | None:
        if before is None or after is None:
            return None
        return round(after - before, 4)

    return StructuralComparison(
        stock_code=stock_code,
        new_groups=tuple(sorted(recent_groups - prior_groups)),
        lost_groups=tuple(sorted(prior_groups - recent_groups)),
        type_share_changes={
            kind: round(recent_shares.get(kind, 0.0) - prior_shares.get(kind, 0.0), 4)
            for kind in types
        },
        high_depth_ratio_change=_delta(
            _high_depth_ratio(prior), _high_depth_ratio(recent)
        ),
        active_week_ratio_change=round(
            _week_ratio(recent, recent_days) - _week_ratio(prior, prior_days), 4
        ),
        single_day_concentration_change=round(
            _concentration(recent) - _concentration(prior), 4
        ),
    )


def build_research_coverage(
    settings: AppSettings,
    storage: Storage,
    *,
    calendar: TradingCalendarService | None = None,
    now: datetime | None = None,
    kinds: tuple[str, ...] | None = None,
) -> ResearchCoverage:
    """Recompute the shared research coverage state from persisted sync cursors.

    Mirrors the run-time coverage built during refresh so the UI can show the
    same cold-start / partial-coverage / calendar-fallback state after the
    refresh finished, without leaking adapter or cursor details.

    ``kinds`` restricts the sync states considered, e.g. ``("research_activity",)``
    so institution boards degrade based on activity-document coverage instead of
    being dragged down by the (much larger) announcement stream backfill.
    """

    calendar_service = calendar or TradingCalendarService(storage)
    now_dt = now or datetime.now(SHANGHAI_TZ)
    requested_start = now_dt.date() - timedelta(days=settings.backfill_days)
    states = [
        storage.get_sync_state(config.key, config.kind)
        for config in settings.research_sources
        if kinds is None or config.kind in kinds
    ]
    states = [state for state in states if state is not None]
    covered_start = min(
        (state.covered_start for state in states if state.covered_start),
        default=None,
    )
    covered_end_raw = max(
        (
            str(state.cursor.get("covered_end"))
            for state in states
            if state.cursor and state.cursor.get("covered_end")
        ),
        default=None,
    )
    covered_end = (
        date.fromisoformat(covered_end_raw) if covered_end_raw else None
    )
    trading_days_covered = (
        calendar_service.trading_day_count_between(covered_start, covered_end)
        if covered_start and covered_end
        else 0
    )
    last_success_at = max(
        (
            state.last_success_at
            for state in states
            if state.last_success_at is not None
        ),
        default=None,
    )
    calendar_states = [
        calendar_service.get_calendar_state(year)
        for year in range(requested_start.year, now_dt.year + 1)
    ]
    calendar_fallback = any(state.calendar_fallback for state in calendar_states)
    calendar_missing = any(state.source is None for state in calendar_states)
    calendar_error = next(
        (
            f"交易日历：{state.last_error}"
            for state in calendar_states
            if state.last_error
        ),
        None,
    )
    reached_cutoff = bool(states) and all(
        state.covered_start is not None and state.covered_start <= requested_start
        for state in states
    )
    provisional = (
        calendar_fallback
        or calendar_missing
        or not states
        or not all(state.last_success_at is not None for state in states)
    )
    return ResearchCoverage(
        requested_start=requested_start,
        covered_start=covered_start,
        covered_end=covered_end,
        trading_days_covered=trading_days_covered,
        sources_scanned=sum(
            1 for state in states if state.last_success_at is not None
        ),
        sources_total=len(settings.research_sources),
        reached_cutoff=reached_cutoff,
        calendar_fallback=calendar_fallback,
        last_success_at=last_success_at,
        provisional=provisional,
        error=(
            next((state.last_error for state in states if state.last_error), None)
            or calendar_error
        ),
    )


@dataclass(frozen=True, slots=True)
class ResearchBoardRunResult:
    documents_scanned: int
    activities_persisted: int
    participants_added: int
    institutions_created: int
    z20_rows: tuple[Z20Row, ...]
    warming_v2_rows: tuple[WarmingV2Row, ...]
    persistence_60_rows: tuple[PersistenceRow, ...]
    persistence_120_rows: tuple[PersistenceRow, ...]
    persistence_rule_60_rows: tuple[PersistenceRow, ...]
    persistence_rule_120_rows: tuple[PersistenceRow, ...]
    comparisons: tuple[StructuralComparison, ...]
    coverage: ResearchCoverage
    errors: tuple[str, ...]
    pipeline_version: str = "v2"


class ResearchBoardService:
    """Institution activity parsing + 20/60/120-day metrics orchestration.

    Runs entirely offline on persisted ``source_documents``; a failing
    document only degrades that activity, never the whole refresh.
    """

    def __init__(
        self,
        settings: AppSettings,
        storage: Storage,
        *,
        registry: InstitutionRegistry | None = None,
        calendar: TradingCalendarService | None = None,
    ) -> None:
        self.settings = settings
        self.storage = storage
        self.registry = registry or InstitutionRegistry(storage)
        self.calendar = calendar or TradingCalendarService(storage)

    def run(
        self,
        *,
        now: datetime,
        publish: bool = True,
        backfill_days: int | None = None,
        pipeline_version: str | None = None,
    ) -> ResearchBoardRunResult:
        """Recompute activities and 20/60/120-day institution metrics.

        ``backfill_days`` overrides ``settings.backfill_days`` (default 200);
        pass 550 for the v2 机构活动基线重算 (plan.md 第三部分 里程碑 4).
        Metric rows are staged per run and only become visible when the
        completed-batch marker advances at the end (``publish=True``); a
        mid-run failure keeps the previously published boards.
        ``pipeline_version`` overrides ``settings.research_pipeline_version``
        ("v2" 默认；"v1" 为发布前兼容口径，v2 里程碑 5 回退/并行比较用)。
        """

        version = pipeline_version or self.settings.research_pipeline_version
        if version not in ("v1", "v2"):
            raise ValueError(
                f"unknown research pipeline version: {version!r}"
            )
        errors: list[str] = []
        documents_scanned = 0
        activities_persisted = 0
        participants_added = 0
        effective_backfill_days = (
            backfill_days
            if backfill_days is not None
            else self.settings.backfill_days
        )
        strict_staging = version == "v2" and effective_backfill_days >= 550
        parsed_staging: list[object] = []

        try:
            documents = self.storage.get_source_documents_between(
                now
                - timedelta(
                    days=(
                        effective_backfill_days
                    )
                ),
                now,
            )
        except Exception as exc:  # noqa: BLE001 - degrade per refresh
            logger.warning("research activity load failed: %s", exc)
            documents = []
            errors.append(str(exc)[:300])

        for document in documents:
            if document.kind != "research_activity" or not (
                document.body_text or ""
            ).strip():
                continue
            documents_scanned += 1
            try:
                parsed = parse_research_activity(
                    document, self.registry, pipeline_version=version
                )
            except Exception as exc:  # noqa: BLE001 - one document only
                logger.warning(
                    "research activity parse failed for %s: %s",
                    document.document_id,
                    exc,
                )
                errors.append(f"{document.document_id}: {str(exc)[:200]}")
                continue
            if parsed is None:
                continue
            if strict_staging:
                parsed_staging.append(parsed)
                continue
            try:
                self._persist_parsed_activity(parsed, version=version, now=now)
                activities_persisted += 1
                participants_added += len(parsed.participants)
            except Exception as exc:  # noqa: BLE001 - one document only
                logger.warning(
                    "research activity persist failed for %s: %s",
                    document.document_id,
                    exc,
                )
                errors.append(f"{document.document_id}: {str(exc)[:200]}")

        if strict_staging and not errors:
            try:
                self.storage.replace_research_activity_bundles(
                    [
                        (
                            parsed.activity,
                            tuple(parsed.evidence_refs),
                            tuple(parsed.participants),
                            tuple(parsed.raw_mentions),
                            tuple(parsed.activity_occurrences),
                            tuple(parsed.participant_occurrences),
                            self._reported_count(parsed, version=version, now=now),
                        )
                        for parsed in parsed_staging
                    ],
                    now,
                )
                activities_persisted = len(parsed_staging)
                participants_added = sum(
                    len(parsed.participants) for parsed in parsed_staging
                )
            except Exception as exc:  # noqa: BLE001 - transaction rolls back
                errors.append(f"staging publish: {str(exc)[:200]}")
        if strict_staging and errors:
            # The previous completed metric batch remains the only published
            # one.  Parsed results stayed in memory until every document had
            # succeeded, so a parser failure cannot mix v1/v2 activity rows.
            publish = False

        end_date = now.date()
        # Institution boards must degrade based on research-activity document
        # coverage, not the much larger announcement backfill (plan.md 13.x):
        # a z-score computed on empty baseline buckets is just the current
        # bucket count and must not be presented as a real acceleration.
        activity_coverage = build_research_coverage(
            self.settings,
            self.storage,
            calendar=self.calendar,
            now=now,
            kinds=("research_activity",),
        )
        trading_days = self.calendar.last_n_trading_days(end_date, 120)
        z20_rows: list[Z20Row] = []
        persistence_60: list[PersistenceRow] = []
        persistence_120: list[PersistenceRow] = []
        comparisons: list[StructuralComparison] = []
        if trading_days:
            full_coverage = (
                len(trading_days) >= 120
                and activity_coverage.trading_days_covered >= 120
            )
            activities = _load_activity_data(self.storage, trading_days[0], end_date)
            by_stock: dict[str, list[_ActivityData]] = defaultdict(list)
            for activity in activities:
                by_stock[activity.stock_code].append(activity)

            buckets = trading_buckets(trading_days)
            current = buckets[-1]
            previous = buckets[:-1]
            industries = self.storage.get_stock_industries(set(by_stock))
            current_counts: dict[str, int] = {}
            for stock, acts in sorted(by_stock.items()):
                current_acts = [
                    activity
                    for activity in acts
                    if current[0] <= activity.end_date <= current[1]
                ]
                if not current_acts:
                    continue
                current_groups: set[str] = set()
                previous_groups: set[str] = set()
                previous_counts: list[int] = []
                for activity in acts:
                    if current[0] <= activity.end_date <= current[1]:
                        current_groups.update(activity.groups)
                    elif any(
                        start <= activity.end_date <= end for start, end in previous
                    ):
                        previous_groups.update(activity.groups)
                for start, end in previous:
                    bucket_groups = {
                        group
                        for activity in acts
                        if start <= activity.end_date <= end
                        for group in activity.groups
                    }
                    previous_counts.append(len(bucket_groups))
                new_groups = current_groups - previous_groups
                analysts: set[str] = set()
                for activity in current_acts:
                    analysts.update(activity.analysts)
                questions = sum(activity.question_count for activity in current_acts)
                high = sum(
                    activity.high_depth_question_count for activity in current_acts
                )
                recent = max(
                    (activity.end_date for activity in current_acts), default=None
                )
                current_counts[stock] = len(current_groups)
                z20_rows.append(
                    Z20Row(
                        stock_code=stock,
                        industry=industries.get(stock),
                        z20=(
                            z20_from_counts(len(current_groups), previous_counts)
                            if full_coverage
                            else None
                        ),
                        current_unique_groups=len(current_groups),
                        new_groups=len(new_groups),
                        analyst_count=len(analysts),
                        high_depth_ratio=round(high / questions, 4) if questions else 0.0,
                        question_count=questions,
                        recent_activity=recent,
                        industry_percentile=None,
                        industry_sample_size=0,
                        provisional=not full_coverage or len(current_groups) == 0,
                    )
                )
            percentiles, sample_sizes = industry_percentiles(
                current_counts, industries
            )
            z20_rows = [
                replace(
                    row,
                    industry_percentile=percentiles.get(row.stock_code),
                    industry_sample_size=sample_sizes.get(row.stock_code, 0),
                )
                for row in z20_rows
            ]
            z20_rows = sort_z20(z20_rows)
            for row in z20_rows:
                self.storage.upsert_institution_metric_snapshot(
                    stock_code=row.stock_code,
                    window_kind="z20",
                    metrics={
                        "z20": row.z20,
                        "current_unique_groups": row.current_unique_groups,
                        "new_groups": row.new_groups,
                        "analyst_count": row.analyst_count,
                        "high_depth_ratio": row.high_depth_ratio,
                        "question_count": row.question_count,
                        "industry_percentile": row.industry_percentile,
                        "industry_sample_size": row.industry_sample_size,
                        "provisional": row.provisional,
                    },
                    window_start=_dt_at(current[0]),
                    window_end=_dt_at(current[1]),
                    snapshot_at=now,
                    publish=False,
                )

            for window_kind, days_needed in (
                ("persistence_60", 60),
                ("persistence_120", 120),
            ):
                days = self.calendar.last_n_trading_days(end_date, days_needed)
                if not days:
                    continue
                start, end = days[0], days[-1]
                rows: list[PersistenceRow] = []
                for stock, acts in sorted(by_stock.items()):
                    included = [
                        activity
                        for activity in acts
                        if start <= activity.end_date <= end
                    ]
                    if not included:
                        continue
                    (
                        active_weeks,
                        active_week_ratio,
                        repeat_followup_ratio,
                        depth_score,
                        concentration,
                        unique_groups,
                        recent,
                    ) = persistence_components(included, days)
                    topics: dict[str, int] = {}
                    for activity in included:
                        for topic, count in activity.topics.items():
                            topics[topic] = topics.get(topic, 0) + count
                    provisional = (
                        activity_coverage.trading_days_covered < days_needed
                        or unique_groups == 0
                    )
                    rows.append(
                        PersistenceRow(
                            stock_code=stock,
                            window_kind=window_kind,
                            persistence_score=persistence_score(
                                active_week_ratio,
                                repeat_followup_ratio,
                                depth_score,
                                concentration,
                            ),
                            active_weeks=active_weeks,
                            active_week_ratio=round(active_week_ratio, 4),
                            unique_groups=unique_groups,
                            repeat_followup_ratio=round(repeat_followup_ratio, 4),
                            depth_score=round(depth_score, 4),
                            single_day_concentration=round(concentration, 4),
                            topics=topics,
                            recent_activity=recent,
                            covered_trading_days=len(days),
                            provisional=provisional,
                        )
                    )
                rows = sort_persistence(rows)
                for row in rows:
                    self.storage.upsert_institution_metric_snapshot(
                        stock_code=row.stock_code,
                        window_kind=row.window_kind,
                        metrics={
                            "persistence_score": row.persistence_score,
                            "active_weeks": row.active_weeks,
                            "active_week_ratio": row.active_week_ratio,
                            "unique_groups": row.unique_groups,
                            "repeat_followup_ratio": row.repeat_followup_ratio,
                            "depth_score": row.depth_score,
                            "single_day_concentration": row.single_day_concentration,
                            "topics": row.topics,
                            "covered_trading_days": row.covered_trading_days,
                            "provisional": row.provisional,
                        },
                        window_start=_dt_at(days[0]),
                        window_end=_dt_at(days[-1]),
                        snapshot_at=now,
                        publish=False,
                    )
                if window_kind == "persistence_60":
                    persistence_60 = rows
                else:
                    persistence_120 = rows
                    if len(days) >= 120:
                        prior_days = days[:60]
                        recent_days = days[-60:]
                        for stock in {row.stock_code for row in rows}:
                            comparison = structural_comparison(
                                stock, by_stock.get(stock, []), prior_days, recent_days
                            )
                            comparisons.append(comparison)
                            self.storage.upsert_institution_metric_snapshot(
                                stock_code=stock,
                                window_kind="persistence_120_detail",
                                metrics={
                                    "new_groups": list(comparison.new_groups),
                                    "lost_groups": list(comparison.lost_groups),
                                    "type_share_changes": comparison.type_share_changes,
                                    "high_depth_ratio_change": (
                                        comparison.high_depth_ratio_change
                                    ),
                                    "active_week_ratio_change": (
                                        comparison.active_week_ratio_change
                                    ),
                                    "single_day_concentration_change": (
                                        comparison.single_day_concentration_change
                                    ),
                                },
                                window_start=_dt_at(prior_days[0]),
                                window_end=_dt_at(recent_days[-1]),
                                snapshot_at=now,
                                publish=False,
                            )

        # ``warming_v2`` is published in parallel with ``z20_legacy`` for one
        # compatibility cycle.  Its 260-day source cohort and formula are
        # independent; no legacy row is overwritten or re-labeled.
        institution_coverage = build_institution_research_coverage(
            self.settings,
            self.storage,
            calendar=self.calendar,
            now=now,
        )
        warming_days = self.calendar.last_n_trading_days(end_date, 260)
        allowed_sources = {
            item.market: frozenset(item.source_keys)
            for item in institution_coverage.market_cohorts
        }
        warming_activities = (
            _load_activity_data(
                self.storage,
                warming_days[0],
                warming_days[-1],
                allowed_sources_by_market=allowed_sources,
            )
            if warming_days
            else []
        )
        warming_industries = self.storage.get_stock_industries(
            {item.stock_code for item in warming_activities}
        )
        warming_rows = warming_v2_rows(
            warming_activities,
            warming_days,
            institution_coverage,
            warming_industries,
            # The ordinary cache is populated from observed stocks and is not
            # proof of a complete listed-company universe.  Keep percentiles
            # empty until M5 validates and explicitly supplies that universe.
            complete_industry_universe=None,
        )
        if len(warming_days) >= 20:
            current_start = warming_days[-20]
            current_end = warming_days[-1]
            for row in warming_rows:
                self.storage.upsert_institution_metric_snapshot(
                    stock_code=row.stock_code,
                    window_kind="warming_20",
                    metrics=row.to_dict(),
                    window_start=_dt_at(current_start),
                    window_end=_dt_at(current_end),
                    snapshot_at=now,
                    publish=False,
                    metric_version="warming_v2",
                    source_cohort_id=row.source_cohort_id,
                )

        persistence_rule_results: dict[str, list[PersistenceRow]] = {
            "persistence_60_v2": [],
            "persistence_120_v2": [],
        }
        warming_by_stock: dict[str, list[_ActivityData]] = defaultdict(list)
        for activity in warming_activities:
            warming_by_stock[activity.stock_code].append(activity)
        for rule_window, days_needed in (
            ("persistence_60_v2", 60),
            ("persistence_120_v2", 120),
        ):
            days = self.calendar.last_n_trading_days(end_date, days_needed)
            if not days:
                continue
            rule_rows: list[PersistenceRow] = []
            for stock, acts in sorted(warming_by_stock.items()):
                included = [
                    activity
                    for activity in acts
                    if days[0] <= activity.end_date <= days[-1]
                ]
                if not included:
                    continue
                components = persistence_rule_components(included, days)
                cohort = cohort_for_stock(institution_coverage, stock)
                coverage_complete = bool(
                    cohort is not None
                    and not cohort.unavailable_source_keys
                    and cohort.trading_days_covered >= days_needed
                )
                index = persistence_rule_index(components)
                if not coverage_complete:
                    index = None
                    reason = "来源共同覆盖不足"
                elif components.unique_groups <= 0:
                    reason = "无合格研究机构"
                elif not components.date_mapping_complete:
                    reason = "机构—日期映射缺失或不可靠"
                elif components.question_data_status == "missing_body":
                    reason = "问答正文缺失"
                elif components.question_data_status == "partial_missing":
                    reason = "部分活动问答正文缺失"
                else:
                    reason = None
                topics: dict[str, int] = {}
                for activity in included:
                    for topic, count in activity.topics.items():
                        topics[topic] = topics.get(topic, 0) + count
                row = PersistenceRow(
                    stock_code=stock,
                    window_kind=rule_window,
                    persistence_score=index,
                    active_weeks=components.active_weeks,
                    active_week_ratio=components.active_week_ratio,
                    unique_groups=components.unique_groups,
                    repeat_followup_ratio=components.repeat_followup_ratio,
                    depth_score=components.depth_score,
                    single_day_concentration=(
                        components.single_day_concentration
                    ),
                    topics=topics,
                    recent_activity=components.recent_activity,
                    covered_trading_days=(
                        min(len(days), cohort.trading_days_covered)
                        if cohort is not None
                        else 0
                    ),
                    provisional=index is None,
                    question_data_status=components.question_data_status,
                    date_mapping_complete=components.date_mapping_complete,
                    metric_version="persistence_rules_v2",
                    provisional_reason=reason,
                    source_cohort_id=(
                        cohort.source_cohort_id if cohort is not None else ""
                    ),
                    date_quality=(
                        next(iter({item.date_quality for item in included}))
                        if len({item.date_quality for item in included}) == 1
                        else "mixed"
                    ),
                    excluded_organization_count=sum(
                        item.excluded_organization_count for item in included
                    ),
                )
                rule_rows.append(row)
            rule_rows = sort_persistence(rule_rows)
            persistence_rule_results[rule_window] = rule_rows
            for row in rule_rows:
                cohort = cohort_for_stock(institution_coverage, row.stock_code)
                self.storage.upsert_institution_metric_snapshot(
                    stock_code=row.stock_code,
                    window_kind=rule_window,
                    metrics=row.to_dict(),
                    window_start=_dt_at(days[0]),
                    window_end=_dt_at(days[-1]),
                    snapshot_at=now,
                    publish=False,
                    metric_version="persistence_rules_v2",
                    source_cohort_id=row.source_cohort_id,
                )

        if publish:
            self.storage.mark_institution_metric_batch(now)
        coverage = self._build_coverage(now, end_date)
        return ResearchBoardRunResult(
            documents_scanned=documents_scanned,
            activities_persisted=activities_persisted,
            participants_added=participants_added,
            institutions_created=self.registry.created_count,
            z20_rows=tuple(z20_rows),
            warming_v2_rows=tuple(warming_rows),
            persistence_60_rows=tuple(persistence_60),
            persistence_120_rows=tuple(persistence_120),
            persistence_rule_60_rows=tuple(
                persistence_rule_results["persistence_60_v2"]
            ),
            persistence_rule_120_rows=tuple(
                persistence_rule_results["persistence_120_v2"]
            ),
            comparisons=tuple(comparisons),
            coverage=coverage,
            errors=tuple(errors),
            pipeline_version=version,
        )

    def _persist_parsed_activity(
        self, parsed, *, version: str, now: datetime
    ) -> None:
        """Persist one already-staged parse result using per-activity swaps."""

        self.storage.upsert_research_activity(parsed.activity, now)
        for ref in parsed.evidence_refs:
            self.storage.upsert_evidence_ref(ref)
        self.storage.replace_research_participants(
            parsed.activity.activity_id, list(parsed.participants)
        )
        self.storage.replace_participant_mentions(
            parsed.activity.activity_id, list(parsed.raw_mentions)
        )
        if version == "v2":
            self.storage.replace_research_occurrences(
                parsed.activity.activity_id,
                list(parsed.activity_occurrences),
                list(parsed.participant_occurrences),
            )
        self.storage.upsert_reported_participant_count(
            self._reported_count(parsed, version=version, now=now)
        )

    def _reported_count(
        self, parsed, *, version: str, now: datetime
    ) -> ReportedParticipantCount:
        if version == "v2":
            named_research = len(
                {
                    participant.institution_id
                    for participant in parsed.participant_occurrences
                    if participant.research_eligible
                }
            )
        else:
            named_research = sum(
                1
                for participant in parsed.participants
                if (
                    self.storage.get_institution(participant.institution_id).institution_type
                    if self.storage.get_institution(participant.institution_id)
                    is not None
                    else ""
                )
                in RESEARCH_INSTITUTION_TYPES
            )
        return ReportedParticipantCount(
            activity_id=parsed.activity.activity_id,
            named_research_count=named_research,
            all_named_org_count=len(parsed.participants),
            reported_institution_count=parsed.activity.reported_participant_count,
            reported_person_count=None,
            evidence_id=None,
            updated_at=now,
        )

    def _build_coverage(self, now: datetime, end_date: date) -> ResearchCoverage:
        return build_research_coverage(
            self.settings,
            self.storage,
            calendar=self.calendar,
            now=now,
        )

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
from dataclasses import dataclass, replace
from datetime import date, datetime, timedelta

from .config import AppSettings, SHANGHAI_TZ
from .institutions import InstitutionRegistry
from .models import (
    PersistenceRow,
    ReportedParticipantCount,
    ResearchCoverage,
    StructuralComparison,
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


def _load_activity_data(
    storage: Storage, start: date, end: date
) -> list[_ActivityData]:
    """Load persisted activities with group/analyst/type aggregation.

    Participant-level metrics are attributed to the activity's end date
    (plan.md 6.3: unsplittable dates use the disclosed activity end date).
    """

    result: list[_ActivityData] = []
    for activity in storage.get_research_activities_between(start, end):
        if not activity.activity_dates:
            continue
        groups: set[str] = set()
        analysts: set[str] = set()
        type_counts: Counter[str] = Counter()
        for participant in storage.get_research_participants(activity.activity_id):
            institution = storage.get_institution(participant.institution_id)
            if institution is None:
                continue
            groups.add(institution.group_id or institution.institution_id)
            if participant.analyst_name:
                analysts.add(participant.analyst_name)
            type_counts[institution.institution_type] += 1
        result.append(
            _ActivityData(
                activity_id=activity.activity_id,
                stock_code=activity.stock_code,
                activity_dates=activity.activity_dates,
                end_date=max(activity.activity_dates),
                groups=frozenset(groups),
                analysts=frozenset(analysts),
                question_count=activity.question_count,
                high_depth_question_count=activity.high_depth_question_count,
                depth_counts=dict(activity.depth_counts or {}),
                topics=dict(activity.topic_counts or {}),
                type_counts=dict(type_counts),
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
            group_dates[group].add(activity.end_date)
            day_groups[activity.end_date].add(group)
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


def sort_persistence(rows: list[PersistenceRow]) -> list[PersistenceRow]:
    """plan.md 13.3 ordering: score -> active weeks -> groups -> recency -> code."""

    return sorted(
        rows,
        key=lambda row: (
            -row.persistence_score,
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
    persistence_60_rows: tuple[PersistenceRow, ...]
    persistence_120_rows: tuple[PersistenceRow, ...]
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

        try:
            documents = self.storage.get_source_documents_between(
                now
                - timedelta(
                    days=(
                        backfill_days
                        if backfill_days is not None
                        else self.settings.backfill_days
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
            try:
                self.storage.upsert_research_activity(parsed.activity, now)
                for ref in parsed.evidence_refs:
                    self.storage.upsert_evidence_ref(ref)
                self.storage.replace_research_participants(
                    parsed.activity.activity_id, list(parsed.participants)
                )
                self.storage.replace_participant_mentions(
                    parsed.activity.activity_id, list(parsed.raw_mentions)
                )
                named_research = sum(
                    1
                    for participant in parsed.participants
                    if (
                        self.storage.get_institution(
                            participant.institution_id
                        ).institution_type
                        if self.storage.get_institution(
                            participant.institution_id
                        )
                        is not None
                        else ""
                    )
                    in RESEARCH_INSTITUTION_TYPES
                )
                self.storage.upsert_reported_participant_count(
                    ReportedParticipantCount(
                        activity_id=parsed.activity.activity_id,
                        named_research_count=named_research,
                        all_named_org_count=len(parsed.participants),
                        reported_institution_count=(
                            parsed.activity.reported_participant_count
                        ),
                        reported_person_count=None,
                        evidence_id=None,
                        updated_at=now,
                    )
                )
                activities_persisted += 1
                participants_added += len(parsed.participants)
            except Exception as exc:  # noqa: BLE001 - one document only
                logger.warning(
                    "research activity persist failed for %s: %s",
                    document.document_id,
                    exc,
                )
                errors.append(f"{document.document_id}: {str(exc)[:200]}")

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

        if publish:
            self.storage.mark_institution_metric_batch(now)
        coverage = self._build_coverage(now, end_date)
        return ResearchBoardRunResult(
            documents_scanned=documents_scanned,
            activities_persisted=activities_persisted,
            participants_added=participants_added,
            institutions_created=self.registry.created_count,
            z20_rows=tuple(z20_rows),
            persistence_60_rows=tuple(persistence_60),
            persistence_120_rows=tuple(persistence_120),
            comparisons=tuple(comparisons),
            coverage=coverage,
            errors=tuple(errors),
            pipeline_version=version,
        )

    def _build_coverage(self, now: datetime, end_date: date) -> ResearchCoverage:
        return build_research_coverage(
            self.settings,
            self.storage,
            calendar=self.calendar,
            now=now,
        )

from __future__ import annotations

from datetime import date, datetime, timedelta

from ashare_hotpot.config import AppSettings, SHANGHAI_TZ
from ashare_hotpot.institution_metrics import (
    ResearchBoardService,
    _ActivityData,
    persistence_rule_components,
    persistence_rule_index,
)
from ashare_hotpot.models import PersistenceRow, SyncCursor
from ashare_hotpot.storage import Storage


NOW = datetime(2026, 8, 10, 18, 0, tzinfo=SHANGHAI_TZ)


def _weekdays(end: date, count: int) -> list[date]:
    rows: list[date] = []
    current = end
    while len(rows) < count:
        if current.weekday() < 5:
            rows.append(current)
        current -= timedelta(days=1)
    return list(reversed(rows))


def _activity(
    day: date,
    *,
    groups: tuple[str, ...] = ("group-a",),
    question_data_status: str = "available",
    question_count: int = 1,
    date_mapping_complete: bool = True,
) -> _ActivityData:
    return _ActivityData(
        activity_id=f"activity-{day}-{question_data_status}",
        stock_code="000001",
        activity_dates=(day,),
        end_date=day,
        groups=frozenset(groups),
        analysts=frozenset(),
        question_count=question_count,
        high_depth_question_count=0,
        depth_counts={"low": question_count, "medium": 0, "high": 0},
        topics={},
        type_counts={},
        group_dates={group: frozenset((day,)) for group in groups},
        source_key="cninfo_research",
        question_data_status=question_data_status,
        date_mapping_complete=date_mapping_complete,
    )


def test_missing_question_body_is_not_the_same_as_low_depth() -> None:
    days = _weekdays(NOW.date(), 60)
    low = persistence_rule_components([_activity(days[-1])], days)
    assert low.question_data_status == "available"
    assert low.depth_score == 0.25
    assert persistence_rule_index(low) is not None

    missing = persistence_rule_components(
        [
            _activity(
                days[-1],
                question_data_status="missing_body",
                question_count=0,
            )
        ],
        days,
    )
    assert missing.question_data_status == "missing_body"
    assert missing.depth_score is None
    assert persistence_rule_index(missing) is None


def test_zero_groups_or_unreliable_date_mapping_returns_no_index() -> None:
    days = _weekdays(NOW.date(), 60)
    no_groups = persistence_rule_components(
        [_activity(days[-1], groups=())], days
    )
    assert no_groups.unique_groups == 0
    assert no_groups.single_day_concentration == 0.0
    assert persistence_rule_index(no_groups) is None

    unreliable = persistence_rule_components(
        [_activity(days[-1], date_mapping_complete=False)], days
    )
    assert unreliable.unique_groups == 1
    assert unreliable.date_mapping_complete is False
    assert persistence_rule_index(unreliable) is None


def test_partial_missing_question_data_invalidates_total_but_keeps_components() -> None:
    days = _weekdays(NOW.date(), 60)
    components = persistence_rule_components(
        [
            _activity(days[-2]),
            _activity(
                days[-1],
                groups=("group-b",),
                question_data_status="missing_body",
                question_count=0,
            ),
        ],
        days,
    )
    assert components.question_data_status == "partial_missing"
    assert components.active_weeks == 2
    assert components.unique_groups == 2
    assert components.depth_score is None
    assert persistence_rule_index(components) is None


def test_service_publishes_rule_index_with_visible_components(monkeypatch, tmp_path) -> None:
    storage = Storage(tmp_path / "hotpot.db")
    storage.initialize()
    days = _weekdays(NOW.date(), 120)
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
    activities = [
        _activity(days[-20]),
        _activity(days[-1], groups=("group-a", "group-b")),
    ]

    def fake_load(_storage, start, end, **_kwargs):
        return [item for item in activities if start <= item.end_date <= end]

    monkeypatch.setattr(
        "ashare_hotpot.institution_metrics._load_activity_data", fake_load
    )
    result = ResearchBoardService(
        AppSettings(app_root=tmp_path), storage
    ).run(now=NOW)
    assert result.persistence_60_rows  # legacy compatibility row
    rule = result.persistence_rule_60_rows[0]
    assert rule.persistence_score is not None
    assert rule.metric_version == "persistence_rules_v2"
    assert rule.question_data_status == "available"
    assert rule.date_mapping_complete is True
    record = storage.get_latest_institution_metric_snapshot_records(
        "persistence_60_v2"
    )["000001"]
    assert record.metric_version == "persistence_rules_v2"
    assert record.metrics["active_week_ratio"] == rule.active_week_ratio
    assert record.metrics["repeat_followup_ratio"] == rule.repeat_followup_ratio
    assert record.metrics["depth_score"] == rule.depth_score
    assert (
        record.metrics["single_day_concentration"]
        == rule.single_day_concentration
    )


def test_persistence_row_round_trip_preserves_missing_reason() -> None:
    row = PersistenceRow(
        stock_code="000001",
        window_kind="persistence_60_v2",
        persistence_score=None,
        active_weeks=1,
        active_week_ratio=0.1,
        unique_groups=1,
        repeat_followup_ratio=0.0,
        depth_score=None,
        single_day_concentration=1.0,
        topics={},
        recent_activity=NOW.date(),
        covered_trading_days=60,
        provisional=True,
        question_data_status="missing_body",
        date_mapping_complete=True,
        metric_version="persistence_rules_v2",
        provisional_reason="问答正文缺失",
    )
    assert PersistenceRow.from_dict(row.to_dict()) == row

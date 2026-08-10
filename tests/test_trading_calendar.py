from __future__ import annotations

from datetime import date, datetime
from pathlib import Path

import pytest

from ashare_hotpot.config import SHANGHAI_TZ
from ashare_hotpot.storage import Storage
from ashare_hotpot.trading_calendar import (
    TradingCalendarService,
    parse_sse_closed_html,
)


FIXTURE = Path(__file__).parent / "fixtures" / "sse_closed_2026.html"


def _load_fixture() -> str:
    return FIXTURE.read_text(encoding="utf-8")


def test_parse_sse_closed_html_extracts_year_and_holiday_dates() -> None:
    year, holidays = parse_sse_closed_html(_load_fixture())

    assert year == 2026
    holiday_set = set(holidays)
    # 元旦：1/1-1/3 休市 + 1/4 周末休市
    assert holiday_set >= {
        date(2026, 1, 1),
        date(2026, 1, 2),
        date(2026, 1, 3),
        date(2026, 1, 4),
    }
    # 春节：2/15-2/23 休市 + 2/14、2/28 周末休市
    assert holiday_set >= {
        date(2026, 2, 14),
        date(2026, 2, 16),
        date(2026, 2, 23),
        date(2026, 2, 28),
    }
    # 国庆节：10/1-10/7 休市 + 9/20、10/10 周末休市
    assert holiday_set >= {
        date(2026, 9, 20),
        date(2026, 10, 1),
        date(2026, 10, 7),
        date(2026, 10, 10),
    }
    assert len(holidays) == 24


def test_parse_sse_closed_html_fails_closed_on_structure_change() -> None:
    with pytest.raises(ValueError, match="年份标签"):
        parse_sse_closed_html("<html><body>没有年份</body></html>")
    with pytest.raises(ValueError, match="休市安排表格"):
        parse_sse_closed_html("<html><body><strong>2026年休市安排</strong></body></html>")
    with pytest.raises(ValueError, match="未解析出任何日期"):
        parse_sse_closed_html(
            '<html><body><strong>2026年休市安排</strong>'
            '<table class="table"><tr><td>元旦：</td><td>无日期内容</td></tr></table>'
            "</body></html>"
        )


def test_calendar_service_generates_and_queries_official_days(tmp_path) -> None:
    storage = Storage(tmp_path / "hotpot.db")
    service = TradingCalendarService(storage)
    now = datetime(2026, 8, 6, 12, 0, tzinfo=SHANGHAI_TZ)

    year, holidays = parse_sse_closed_html(_load_fixture())
    service.ensure_year_from_holidays(year, holidays, updated_at=now)

    state = service.get_calendar_state(2026)
    assert state.source == "sse"
    assert state.calendar_fallback is False
    assert state.last_success_at == now
    assert state.trading_day_count > 240

    assert service.is_trading_day(date(2026, 1, 5)) is True
    assert service.is_trading_day(date(2026, 1, 2)) is False  # 元旦休市
    assert service.is_trading_day(date(2026, 10, 2)) is False  # 国庆休市
    assert service.is_trading_day(date(2026, 8, 8)) is False  # 周六

    week = service.trading_days_between(date(2026, 1, 1), date(2026, 1, 9))
    assert week == [date(2026, 1, 5), date(2026, 1, 6), date(2026, 1, 7), date(2026, 1, 8), date(2026, 1, 9)]
    assert service.trading_day_count_between(date(2026, 1, 1), date(2026, 1, 9)) == 5


def test_calendar_service_fallback_is_provisional(tmp_path) -> None:
    storage = Storage(tmp_path / "hotpot.db")
    service = TradingCalendarService(storage)
    now = datetime(2026, 8, 6, 12, 0, tzinfo=SHANGHAI_TZ)

    service.ensure_year_fallback(2026, updated_at=now)

    state = service.get_calendar_state(2026)
    assert state.source == "fallback"
    assert state.calendar_fallback is True
    assert state.last_success_at == now
    # 周一至周五都开市，即使国庆当天也按交易日计算。
    assert service.is_trading_day(date(2026, 10, 1)) is True
    assert service.is_trading_day(date(2026, 8, 8)) is False
    assert state.trading_day_count == 261


def test_calendar_service_last_n_trading_days_and_cold_start(tmp_path) -> None:
    storage = Storage(tmp_path / "hotpot.db")
    service = TradingCalendarService(storage)
    now = datetime(2026, 8, 6, 12, 0, tzinfo=SHANGHAI_TZ)

    # Cold start: no calendar rows yet.
    assert service.last_n_trading_days(date(2026, 8, 6), 5) == []
    assert service.trading_day_count_between(date(2026, 1, 1), date(2026, 12, 31)) == 0
    cold = service.get_calendar_state(2026)
    assert cold.source is None
    assert cold.calendar_fallback is False
    assert cold.trading_day_count == 0

    service.ensure_year_from_holidays(
        2026, [date(2026, 1, 1)], updated_at=now
    )
    last_five = service.last_n_trading_days(date(2026, 1, 9), 5)
    assert last_five == [
        date(2026, 1, 5),
        date(2026, 1, 6),
        date(2026, 1, 7),
        date(2026, 1, 8),
        date(2026, 1, 9),
    ]
    assert service.last_n_trading_days(date(2026, 1, 9), 0) == []


def test_calendar_service_records_failed_sync(tmp_path) -> None:
    storage = Storage(tmp_path / "hotpot.db")
    service = TradingCalendarService(storage)
    now = datetime(2026, 8, 6, 12, 0, tzinfo=SHANGHAI_TZ)

    service.mark_calendar_error(2026, "请求超时", updated_at=now)

    state = service.get_calendar_state(2026)
    assert state.source is None
    assert state.calendar_fallback is False
    assert state.last_success_at is None
    assert state.last_error == "请求超时"

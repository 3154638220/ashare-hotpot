from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date, datetime, timedelta

from bs4 import BeautifulSoup

from .config import SHANGHAI_TZ
from .models import SyncCursor
from .storage import Storage


SSE_CLOSED_URL = "https://www.sse.com.cn/disclosure/dealinstruc/closed/"
CALENDAR_SOURCE_KEY = "trading_calendar"

_YEAR_RE = re.compile(r"(\d{4})年休市安排")
_RANGE_RE = re.compile(
    r"(\d{1,2})月(\d{1,2})日（星期[一二三四五六日天]）"
    r"至(\d{1,2})月(\d{1,2})日（星期[一二三四五六日天]）休市"
)
_DATE_TOKEN_RE = re.compile(r"(\d{1,2})月(\d{1,2})日（星期[一二三四五六日天]）")


def parse_sse_closed_html(html: str) -> tuple[int, tuple[date, ...]]:
    """Parse the SSE annual closed-market schedule page.

    Returns ``(year, holiday_dates)``.  Both the inclusive ``X月X日 ... 至
    X月X日 ... 休市`` ranges and the explicit ``为/是周末休市`` single days
    are treated as closed days; every other weekday is a trading day.

    Raises :class:`ValueError` when the year label, the schedule table, or
    any parseable holiday is missing (fail closed on structure changes).
    """

    year_match = _YEAR_RE.search(html)
    if year_match is None:
        raise ValueError("未找到“N年休市安排”年份标签，页面结构可能已变化")
    year = int(year_match.group(1))

    soup = BeautifulSoup(html, "html.parser")
    table = soup.find("table", class_="table")
    if table is None:
        raise ValueError("未找到休市安排表格，页面结构可能已变化")

    holidays: set[date] = set()
    for row in table.find_all("tr"):
        cells = [cell.get_text(" ", strip=True) for cell in row.find_all("td")]
        if not cells:
            continue
        text = cells[-1]
        for match in _RANGE_RE.finditer(text):
            start = date(year, int(match.group(1)), int(match.group(2)))
            end = date(year, int(match.group(3)), int(match.group(4)))
            if end < start:
                raise ValueError(f"休市区间日期倒置：{start} 至 {end}")
            day = start
            while day <= end:
                holidays.add(day)
                day += timedelta(days=1)
        for sentence in re.split(r"[。；]", text):
            if "周末休市" not in sentence:
                continue
            if not ("为" in sentence or "是" in sentence):
                continue
            for match in _DATE_TOKEN_RE.finditer(sentence):
                holidays.add(date(year, int(match.group(1)), int(match.group(2))))

    if not holidays:
        raise ValueError("休市安排中未解析出任何日期，页面结构可能已变化")
    return year, tuple(sorted(holidays))


@dataclass(frozen=True, slots=True)
class CalendarState:
    """Persisted calendar status of one year for coverage display."""

    year: int
    source: str | None  # sse | fallback | None
    calendar_fallback: bool
    trading_day_count: int
    last_success_at: datetime | None
    last_error: str | None


class TradingCalendarService:
    """Storage-backed trading calendar used for all 20/60/120-day windows.

    Ranking code must consume window boundaries through this service and must
    never approximate trading days with natural days.
    """

    def __init__(self, storage: Storage) -> None:
        self.storage = storage

    @staticmethod
    def _all_days_in_year(year: int) -> Iterable[date]:
        day = date(year, 1, 1)
        end = date(year, 12, 31)
        while day <= end:
            yield day
            day += timedelta(days=1)

    def ensure_year_from_holidays(
        self,
        year: int,
        holidays: Iterable[date],
        *,
        updated_at: datetime | None = None,
    ) -> None:
        """Generate and persist the official trading calendar for a year.

        Trading days are weekdays (Monday–Friday) that are not listed as
        holidays.  Persists with ``source='sse'`` and a successful sync state.
        """

        holiday_set = {day for day in holidays if day.year == year}
        trading_days = [
            day
            for day in self._all_days_in_year(year)
            if day.weekday() < 5 and day not in holiday_set
        ]
        now = updated_at or datetime.now(SHANGHAI_TZ)
        self.storage.replace_trading_days(
            year, trading_days, source="sse", updated_at=now
        )
        self._save_calendar_sync(year, source="sse", last_error=None, updated_at=now)

    def ensure_year_fallback(
        self, year: int, *, updated_at: datetime | None = None
    ) -> None:
        """Persist a Monday–Friday fallback calendar and mark it provisional."""

        trading_days = [
            day for day in self._all_days_in_year(year) if day.weekday() < 5
        ]
        now = updated_at or datetime.now(SHANGHAI_TZ)
        self.storage.replace_trading_days(
            year, trading_days, source="fallback", updated_at=now
        )
        self._save_calendar_sync(
            year, source="fallback", last_error=None, updated_at=now
        )

    def mark_calendar_error(
        self, year: int, error: str, *, updated_at: datetime | None = None
    ) -> None:
        """Record a failed calendar sync for a year without touching cache."""

        now = updated_at or datetime.now(SHANGHAI_TZ)
        self._save_calendar_sync(year, source=None, last_error=error, updated_at=now)

    def _save_calendar_sync(
        self,
        year: int,
        *,
        source: str | None,
        last_error: str | None,
        updated_at: datetime,
    ) -> None:
        cursor = SyncCursor(
            source_key=CALENDAR_SOURCE_KEY,
            sync_kind=str(year),
            cursor={"source": source} if source else None,
            target_start=None,
            covered_start=None,
            last_success_at=updated_at if source and not last_error else None,
            last_error=last_error,
            updated_at=updated_at,
        )
        self.storage.save_sync_state(cursor)

    def get_calendar_state(self, year: int) -> CalendarState:
        source = self.storage.get_trading_day_source(year)
        cursor = self.storage.get_sync_state(CALENDAR_SOURCE_KEY, str(year))
        trading_day_count = (
            self.storage.trading_day_count_between(
                date(year, 1, 1), date(year, 12, 31)
            )
            if source
            else 0
        )
        return CalendarState(
            year=year,
            source=source,
            calendar_fallback=source == "fallback",
            trading_day_count=trading_day_count,
            last_success_at=cursor.last_success_at if cursor else None,
            last_error=cursor.last_error if cursor else None,
        )

    def is_trading_day(self, day: date) -> bool:
        return self.storage.is_trading_day(day)

    def trading_days_between(self, start: date, end: date) -> list[date]:
        return self.storage.get_trading_days_between(start, end)

    def trading_day_count_between(self, start: date, end: date) -> int:
        return self.storage.trading_day_count_between(start, end)

    def last_n_trading_days(self, end: date, n: int) -> list[date]:
        """The up-to-``n`` most recent cached trading days at/before ``end``.

        A cold-start calendar may return fewer than ``n`` days; callers must
        treat the shortfall as provisional coverage.
        """

        if n <= 0:
            return []
        days = self.storage.get_trading_days_between(end - timedelta(days=730), end)
        return days[-n:]

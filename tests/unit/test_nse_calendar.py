"""Tests for :class:`ml4t.india.calendar.nse.NSECalendar`."""

from __future__ import annotations

import datetime as dt

import pytest

from ml4t.india.calendar import NSECalendar, nse_calendar, session_bounds

# Known dates (2026) per BSEExchangeCalendar:
# 2026-01-26 (Mon) Republic Day -- NSE holiday.
# 2026-04-22 (Wed) is a normal session.
# 2026-04-25 (Sat) is a weekend.


@pytest.fixture
def cal() -> NSECalendar:
    return NSECalendar()


class TestSingleton:
    def test_nse_calendar_returns_same_instance(self) -> None:
        assert nse_calendar() is nse_calendar()

    def test_timezone_is_asia_calcutta(self) -> None:
        # pandas-market-calendars uses the legacy name "Asia/Calcutta" for IST;
        # functionally equivalent to "Asia/Kolkata".
        cal = nse_calendar()
        assert "Asia" in cal.timezone


class TestIsSessionDay:
    def test_normal_weekday_is_session(self, cal: NSECalendar) -> None:
        assert cal.is_session_day(dt.date(2026, 4, 22)) is True

    def test_saturday_is_not_session(self, cal: NSECalendar) -> None:
        assert cal.is_session_day(dt.date(2026, 4, 25)) is False

    def test_sunday_is_not_session(self, cal: NSECalendar) -> None:
        assert cal.is_session_day(dt.date(2026, 4, 26)) is False

    def test_republic_day_is_not_session(self, cal: NSECalendar) -> None:
        """Republic Day (Jan 26) is a declared NSE holiday."""
        assert cal.is_session_day(dt.date(2026, 1, 26)) is False


class TestNextSession:
    def test_from_friday_goes_to_monday(self, cal: NSECalendar) -> None:
        # 2026-04-24 is Friday -> next session is Monday 2026-04-27.
        nxt = cal.next_session(dt.date(2026, 4, 24))
        assert nxt == dt.date(2026, 4, 27)

    def test_strictly_after(self, cal: NSECalendar) -> None:
        """The 'after' date itself is NOT returned even if it's a session."""
        # 2026-04-22 (Wed) is a session; strict next must be 2026-04-23.
        nxt = cal.next_session(dt.date(2026, 4, 22))
        assert nxt == dt.date(2026, 4, 23)


class TestPreviousSession:
    def test_from_saturday_goes_to_friday(self, cal: NSECalendar) -> None:
        prev = cal.previous_session(dt.date(2026, 4, 25))
        assert prev == dt.date(2026, 4, 24)

    def test_from_monday_goes_to_friday(self, cal: NSECalendar) -> None:
        prev = cal.previous_session(dt.date(2026, 4, 27))
        assert prev == dt.date(2026, 4, 24)


class TestSessionBounds:
    def test_open_and_close_are_ist_aware(self, cal: NSECalendar) -> None:
        open_t, close_t = cal.session_bounds(dt.date(2026, 4, 22))
        # IST is UTC+5:30
        assert open_t.utcoffset() == dt.timedelta(hours=5, minutes=30)
        assert close_t.utcoffset() == dt.timedelta(hours=5, minutes=30)

    def test_nse_regular_hours_09_15_to_15_30(self, cal: NSECalendar) -> None:
        open_t, close_t = cal.session_bounds(dt.date(2026, 4, 22))
        assert (open_t.hour, open_t.minute) == (9, 15)
        assert (close_t.hour, close_t.minute) == (15, 30)

    def test_non_session_raises(self, cal: NSECalendar) -> None:
        with pytest.raises(ValueError, match="not a trading session"):
            cal.session_bounds(dt.date(2026, 4, 25))  # Saturday

    def test_module_level_shortcut(self) -> None:
        open_t, close_t = session_bounds(dt.date(2026, 4, 22))
        assert open_t < close_t


class TestSessionsInRange:
    def test_five_weekdays_yield_five_sessions(self, cal: NSECalendar) -> None:
        # 2026-04-20 Mon through 2026-04-24 Fri, no holidays that week.
        sessions = cal.sessions_in_range(dt.date(2026, 4, 20), dt.date(2026, 4, 24))
        assert sessions == [
            dt.date(2026, 4, 20),
            dt.date(2026, 4, 21),
            dt.date(2026, 4, 22),
            dt.date(2026, 4, 23),
            dt.date(2026, 4, 24),
        ]

    def test_weekend_only_range_is_empty(self, cal: NSECalendar) -> None:
        sessions = cal.sessions_in_range(dt.date(2026, 4, 25), dt.date(2026, 4, 26))
        assert sessions == []

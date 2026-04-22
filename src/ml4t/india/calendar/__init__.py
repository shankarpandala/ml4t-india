""":mod:`ml4t.india.calendar` -- NSE/BSE trading-session awareness.

Thin wrapper around :class:`pandas_market_calendars.BSEExchangeCalendar`
(which covers both NSE and BSE, since the two equity cash markets
share session hours and holidays):

* :class:`NSECalendar` exposes ``is_session_day``, ``next_session``,
  ``previous_session`` and ``session_bounds`` with IST-aware
  :class:`datetime.datetime` returns -- so strategies don't have to
  think about UTC round-trips.

Used by live-trading startup checks (don't launch on a holiday), by
backtest date-range generators, and by diagnostic reports.
"""

from __future__ import annotations

from ml4t.india.calendar.nse import (
    NSECalendar,
    nse_calendar,
    session_bounds,
)

__all__ = [
    "NSECalendar",
    "nse_calendar",
    "session_bounds",
]

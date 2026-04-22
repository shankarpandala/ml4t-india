"""NSE / BSE trading-session helpers via :mod:`pandas_market_calendars`.

pandas_market_calendars ships a :class:`BSEExchangeCalendar` that covers
India's equity cash session (09:15 - 15:30 IST) and the full NSE holiday
schedule; NSE and BSE share session hours, so one calendar serves both.

This module wraps it with an ergonomic surface:

* Native :class:`datetime.datetime` / :class:`datetime.date` returns in
  Asia/Kolkata (IST), not pandas Timestamps -- callers don't have to
  think about timezone coercion.
* Stateless helpers (``is_session_day``, ``next_session``, etc.) plus a
  pre-built calendar singleton for the hot path.
* Deterministic: every helper takes a date or datetime, the calendar
  decides; no "today" surprises from implicit ``dt.date.today()`` calls.
"""

from __future__ import annotations

import datetime as dt

import pandas as pd
import pandas_market_calendars as mcal

_IST = dt.timezone(dt.timedelta(hours=5, minutes=30), name="IST")


class NSECalendar:
    """Wrapper around :class:`pandas_market_calendars.BSEExchangeCalendar`.

    Exposes the subset of the upstream calendar ml4t-india cares about:

    * Session-day checks (``is_session_day``).
    * Next / previous session from an arbitrary date.
    * Session open/close for a given date.

    Parameters
    ----------
    name:
        Calendar name passed to :func:`pandas_market_calendars.get_calendar`.
        Defaults to ``"BSE"`` (covers both NSE + BSE equity cash). A caller
        who needs NFO/BFO derivatives hours can pass ``"NSE"`` when
        upstream introduces it; today both resolve to the same hours.
    """

    def __init__(self, name: str = "BSE") -> None:
        self._cal = mcal.get_calendar(name)
        self.name = name

    # ---- low-level underlying exposure -----------------------------

    @property
    def calendar(self) -> mcal.MarketCalendar:
        """The wrapped :class:`pandas_market_calendars.MarketCalendar`."""
        return self._cal

    @property
    def timezone(self) -> str:
        """Calendar timezone (string name as pandas publishes it)."""
        return str(self._cal.tz)

    # ---- session queries -------------------------------------------

    def is_session_day(self, day: dt.date) -> bool:
        """Return ``True`` iff ``day`` is a regular trading session.

        Weekends + declared NSE holidays return ``False``. Half-day
        sessions (Muhurat on Diwali, etc.) are considered session days.
        """
        sessions = self._sessions_df(day, day)
        return not sessions.empty

    def next_session(self, after: dt.date) -> dt.date:
        """Return the next session date strictly after ``after``.

        Looks up to 30 calendar days forward, which is more than enough to
        clear any NSE holiday stretch (longest known is 4 consecutive
        days + weekends).
        """
        start = after + dt.timedelta(days=1)
        end = after + dt.timedelta(days=30)
        sessions = self._sessions_df(start, end)
        if sessions.empty:
            raise ValueError(f"no trading session within 30 days after {after}")
        return sessions.index[0].date()

    def previous_session(self, before: dt.date) -> dt.date:
        """Return the previous session date strictly before ``before``."""
        start = before - dt.timedelta(days=30)
        end = before - dt.timedelta(days=1)
        sessions = self._sessions_df(start, end)
        if sessions.empty:
            raise ValueError(f"no trading session within 30 days before {before}")
        return sessions.index[-1].date()

    def session_bounds(
        self,
        day: dt.date,
    ) -> tuple[dt.datetime, dt.datetime]:
        """Return ``(open_ist, close_ist)`` for ``day`` as IST datetimes.

        Raises :class:`ValueError` if ``day`` is not a session day.
        """
        sessions = self._sessions_df(day, day)
        if sessions.empty:
            raise ValueError(f"{day} is not a trading session")
        row = sessions.iloc[0]
        return (
            _to_ist(row["market_open"]),
            _to_ist(row["market_close"]),
        )

    def sessions_in_range(
        self,
        start: dt.date,
        end: dt.date,
    ) -> list[dt.date]:
        """List trading dates in the inclusive range ``[start, end]``."""
        sessions = self._sessions_df(start, end)
        return [ts.date() for ts in sessions.index]

    # ---- internals -------------------------------------------------

    def _sessions_df(self, start: dt.date, end: dt.date) -> pd.DataFrame:
        """Raw schedule DataFrame between start and end (inclusive)."""
        return self._cal.schedule(start_date=start, end_date=end)


# Module-level singleton for the hot path. Importing
# pandas_market_calendars is non-trivial (~200ms on first load); building
# one up front lets callers avoid the cost.
_DEFAULT = NSECalendar()


def nse_calendar() -> NSECalendar:
    """Return the default NSE/BSE calendar singleton."""
    return _DEFAULT


def session_bounds(day: dt.date) -> tuple[dt.datetime, dt.datetime]:
    """Module-level shortcut for ``nse_calendar().session_bounds(day)``."""
    return _DEFAULT.session_bounds(day)


# ---- helpers ------------------------------------------------------------


def _to_ist(value: pd.Timestamp | dt.datetime) -> dt.datetime:
    """Coerce a pandas.Timestamp or naive/aware datetime to IST-aware datetime."""
    if isinstance(value, pd.Timestamp):
        value = value.to_pydatetime()
    if value.tzinfo is None:
        value = value.replace(tzinfo=dt.UTC)
    return value.astimezone(_IST)


__all__ = [
    "NSECalendar",
    "nse_calendar",
    "session_bounds",
]

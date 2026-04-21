"""Concrete :class:`IndianOHLCVProvider` backed by the Zerodha Kite API.

:class:`KiteProvider` is the first non-abstract ml4t-india data adapter.
It combines three Phase-1 pieces:

* :class:`~ml4t.india.kite.client.KiteClient` -- rate-limited, error-
  translated facade over the kiteconnect SDK.
* :class:`~ml4t.india.kite.instruments.InstrumentsCache` -- maps
  ``(tradingsymbol, exchange)`` -> ``instrument_token``.
* :class:`~ml4t.india.data.IndianOHLCVProvider` -- upstream template-
  method base; inherits rate-limit / circuit-breaker / validation
  pipeline from :class:`ml4t.data.providers.base.BaseProvider`.

Phase 2.1 ships the core: resolve symbol, fetch a single historical
window, return the canonical OHLCV frame. Phase 2.3 adds windowing for
long ranges (Kite caps minute data to 60-day requests, daily to 2000-
day requests, etc.).
"""

from __future__ import annotations

import datetime as dt
from typing import ClassVar

import polars as pl

from ml4t.india.core.constants import Exchange
from ml4t.india.data.base import IndianOHLCVProvider
from ml4t.india.kite.client import KiteClient
from ml4t.india.kite.instruments import InstrumentsCache

# Map ml4t-data frequency strings (canonical across all providers) to the
# interval values Kite's historical_data endpoint expects.
_FREQUENCY_MAP: dict[str, str] = {
    "daily": "day",
    "day": "day",
    "1d": "day",
    "1min": "minute",
    "minute": "minute",
    "3min": "3minute",
    "3minute": "3minute",
    "5min": "5minute",
    "5minute": "5minute",
    "10min": "10minute",
    "10minute": "10minute",
    "15min": "15minute",
    "15minute": "15minute",
    "30min": "30minute",
    "30minute": "30minute",
    "60min": "60minute",
    "60minute": "60minute",
    "1h": "60minute",
    "hour": "60minute",
}

# Kite's per-request date-range ceilings (inclusive days) by interval.
# Source: https://kite.trade/docs/connect/v3/historical/#availability
# These are conservative; exceeding them yields an empty response or
# HTTP 400 from the SDK.
_MAX_DAYS_PER_REQUEST: dict[str, int] = {
    "minute": 60,
    "3minute": 100,
    "5minute": 100,
    "10minute": 100,
    "15minute": 200,
    "30minute": 200,
    "60minute": 400,
    "day": 2000,
}


class KiteProvider(IndianOHLCVProvider):
    """OHLCV provider backed by Zerodha Kite's ``historical_data`` endpoint.

    Parameters
    ----------
    client:
        A :class:`~ml4t.india.kite.client.KiteClient` (or test double with
        a matching ``historical_data(...)`` method). Its rate limiter
        handles the 3 req/s ceiling for the historical category.
    instruments:
        An :class:`~ml4t.india.kite.instruments.InstrumentsCache` already
        refreshed with today's dump. :meth:`_fetch_and_transform_data`
        calls :meth:`InstrumentsCache.resolve` to translate the symbol
        into the numeric ``instrument_token`` Kite requires.
    default_exchange:
        Optional default exchange for symbols that trade on more than
        one venue (e.g. ``RELIANCE`` on NSE and BSE). Used when the
        caller passes a bare symbol; specific lookups can still pin the
        exchange explicitly via the symbol spec ``EXCHANGE:SYMBOL``.
    """

    SUPPORTED_EXCHANGES: ClassVar[frozenset[Exchange]] = frozenset(
        {
            Exchange.NSE,
            Exchange.BSE,
            Exchange.NFO,
            Exchange.BFO,
            Exchange.CDS,
            Exchange.BCD,
            Exchange.MCX,
        }
    )

    def __init__(
        self,
        client: KiteClient,
        instruments: InstrumentsCache,
        default_exchange: Exchange | str = Exchange.NSE,
    ) -> None:
        super().__init__()
        self._client = client
        self._instruments = instruments
        self._default_exchange = str(default_exchange)

    @property
    def name(self) -> str:
        return "kite"

    # ---- upstream template-method hook ---------------------------------

    def _fetch_and_transform_data(
        self,
        symbol: str,
        start: str,
        end: str,
        frequency: str = "daily",
    ) -> pl.DataFrame:
        """Resolve ``symbol``, fetch candles (chunked if needed), return frame.

        ``symbol`` may be either the bare tradingsymbol (``"RELIANCE"``)
        or ``EXCHANGE:SYMBOL`` (``"BSE:RELIANCE"``); the latter pins the
        exchange, the former falls back to
        :attr:`KiteProvider._default_exchange`.

        For requests longer than Kite's documented per-interval ceiling
        (``_MAX_DAYS_PER_REQUEST``), the range is split into successive
        windows and the per-chunk frames concatenated into a single
        OHLCV frame. Each chunk draws one token from the
        :class:`~ml4t.india.kite.rate_limit.KiteRateLimiter` historical
        bucket (3 req/s), so multi-year minute requests are throttled
        automatically.
        """
        tradingsymbol, exchange = self._split_symbol(symbol)
        meta = self._instruments.resolve(tradingsymbol, exchange=exchange)
        kite_interval = _FREQUENCY_MAP.get(frequency, frequency)

        frames: list[pl.DataFrame] = []
        for chunk_start, chunk_end in _chunk_date_range(
            start, end, _MAX_DAYS_PER_REQUEST.get(kite_interval, 2000)
        ):
            raw = self._client.historical_data(
                instrument_token=meta.instrument_token,
                from_date=chunk_start,
                to_date=chunk_end,
                interval=kite_interval,
            )
            frames.append(_kite_candles_to_frame(raw, symbol=tradingsymbol))

        if not frames:
            return _kite_candles_to_frame([], symbol=tradingsymbol)
        if len(frames) == 1:
            return frames[0]
        return pl.concat(frames, how="vertical").unique(
            subset=["timestamp"], maintain_order=True
        )

    # ---- helpers -------------------------------------------------------

    def _split_symbol(self, symbol: str) -> tuple[str, str]:
        """Parse ``EXCHANGE:SYMBOL`` or bare ``SYMBOL`` -> (symbol, exchange)."""
        if ":" in symbol:
            exchange, tradingsymbol = symbol.split(":", 1)
            return tradingsymbol, exchange
        return symbol, self._default_exchange


# ----------------------------------------------------------------------
# helpers
# ----------------------------------------------------------------------


def _kite_candles_to_frame(
    rows: list[dict[str, object]] | list[list[object]],
    symbol: str,
) -> pl.DataFrame:
    """Convert Kite's historical_data payload into the canonical OHLCV frame.

    Kite returns a list of dicts (via the kiteconnect SDK) or a list of
    lists (raw API); we handle both shapes here so :class:`FakeKiteClient`
    tests can seed either format.
    """
    if not rows:
        return pl.DataFrame(
            schema={
                "timestamp": pl.Datetime,
                "symbol": pl.Utf8,
                "open": pl.Float64,
                "high": pl.Float64,
                "low": pl.Float64,
                "close": pl.Float64,
                "volume": pl.Float64,
            }
        )

    first = rows[0]
    if isinstance(first, dict):
        records = [
            {
                "timestamp": _coerce_timestamp(r.get("date") or r.get("timestamp")),
                "open": float(r["open"]),
                "high": float(r["high"]),
                "low": float(r["low"]),
                "close": float(r["close"]),
                "volume": float(r.get("volume", 0) or 0),
            }
            for r in rows  # type: ignore[union-attr]
        ]
    else:
        # list[list]: [timestamp, open, high, low, close, volume]
        records = [
            {
                "timestamp": _coerce_timestamp(r[0]),
                "open": float(r[1]),
                "high": float(r[2]),
                "low": float(r[3]),
                "close": float(r[4]),
                "volume": float(r[5]) if len(r) > 5 else 0.0,
            }
            for r in rows  # type: ignore[index]
        ]

    df = pl.DataFrame(records)
    return df.with_columns(pl.lit(symbol.upper()).alias("symbol")).select(
        ["timestamp", "symbol", "open", "high", "low", "close", "volume"]
    )


def _coerce_timestamp(value: object) -> dt.datetime:
    """Coerce a Kite timestamp (str / datetime) to a naive IST datetime.

    Kite returns ISO-8601 strings with ``+05:30`` offsets when requests
    cross multiple days, and naive ``datetime`` objects for single-day
    requests. Polars refuses to auto-parse timezone-mixed inputs, so we
    normalise upstream. Naive datetimes are assumed to be IST already
    (Kite's documented behaviour).
    """
    if isinstance(value, dt.datetime):
        # Strip tzinfo if present; downstream callers convert to IST.
        return value.replace(tzinfo=None)
    if isinstance(value, str):
        parsed = dt.datetime.fromisoformat(value)
        return parsed.replace(tzinfo=None)
    raise TypeError(f"unsupported timestamp type: {type(value).__name__}")


def _chunk_date_range(
    start: str, end: str, max_days: int
) -> list[tuple[str, str]]:
    """Split ``start..end`` (inclusive) into windows of at most ``max_days`` each.

    Inputs can be ``YYYY-MM-DD`` or ``YYYY-MM-DD HH:MM:SS``; outputs mirror
    the input format. Empty and single-window cases fall through as one
    tuple; no rounding is applied.
    """
    start_dt = _parse_input_date(start)
    end_dt = _parse_input_date(end)
    if start_dt > end_dt:
        return []

    span = (end_dt - start_dt).days
    if span <= max_days:
        return [(start, end)]

    chunks: list[tuple[str, str]] = []
    cursor = start_dt
    delta = dt.timedelta(days=max_days)
    while cursor <= end_dt:
        chunk_end = min(cursor + delta, end_dt)
        chunks.append((cursor.date().isoformat(), chunk_end.date().isoformat()))
        if chunk_end >= end_dt:
            break
        cursor = chunk_end + dt.timedelta(days=1)
    return chunks


def _parse_input_date(value: str) -> dt.datetime:
    """Parse a ``YYYY-MM-DD`` or ``YYYY-MM-DD HH:MM:SS`` string into a datetime."""
    try:
        return dt.datetime.fromisoformat(value)
    except ValueError:
        return dt.datetime.strptime(value, "%Y-%m-%d")


__all__ = ["KiteProvider"]

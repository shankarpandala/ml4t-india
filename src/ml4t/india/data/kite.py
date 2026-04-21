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
        """Resolve ``symbol``, fetch candles, return the canonical frame.

        ``symbol`` may be either the bare tradingsymbol (``"RELIANCE"``)
        or ``EXCHANGE:SYMBOL`` (``"BSE:RELIANCE"``); the latter pins the
        exchange, the former falls back to
        :attr:`KiteProvider._default_exchange`.

        Phase-2.1 ships a single-window fetch (no chunking). A request
        for >60 days of 1-minute candles will fail at the Kite endpoint;
        the windowing layer in Phase 2.3 will split those ranges before
        calling here.
        """
        tradingsymbol, exchange = self._split_symbol(symbol)
        meta = self._instruments.resolve(tradingsymbol, exchange=exchange)
        kite_interval = _FREQUENCY_MAP.get(frequency, frequency)

        raw = self._client.historical_data(
            instrument_token=meta.instrument_token,
            from_date=start,
            to_date=end,
            interval=kite_interval,
        )
        return _kite_candles_to_frame(raw, symbol=tradingsymbol)

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


__all__ = ["KiteProvider"]

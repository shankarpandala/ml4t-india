"""Zerodha Kite instruments dump: fetch, cache, resolve.

Kite Connect publishes a daily CSV dump of every tradable instrument
(``GET /instruments``) with columns::

    instrument_token, exchange_token, tradingsymbol, name, last_price,
    expiry, strike, tick_size, lot_size, instrument_type, segment,
    exchange

The dump is refreshed once per day (around 08:30 IST) and is used to
translate human-readable symbols like ``"RELIANCE"`` (NSE) into the
numeric ``instrument_token`` Kite's other endpoints require. We cache
it locally so:

* Repeated resolves never hit the network.
* Downstream providers / brokers / feeds share a single canonical
  lookup surface.
* Tests can inject a fixture dump without touching Kite at all.

This module exposes :class:`InstrumentMeta` (one row of the dump) and
:class:`InstrumentsCache` (persistence + lookups). The cache is
backed by Polars + Parquet under ``~/.ml4t/india/instruments/``; each
day lands in its own file (``YYYY-MM-DD.parquet``) so switching days
is cheap and historical caches stay available for diagnostics.

Phase 1 ships the mechanics. Phase 2 (``KiteProvider``) and Phase 4
(``KiteBroker``) both depend on :meth:`InstrumentsCache.resolve` to
translate symbols.
"""

from __future__ import annotations

import datetime as dt
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

import polars as pl

from ml4t.india.core.exceptions import DataIntegrityError, InstrumentNotFoundError

_IST = dt.timezone(dt.timedelta(hours=5, minutes=30), name="IST")

#: Hour (IST) after which Kite publishes the day's refreshed dump.
#: Refreshing a cache before this is pointless; after, the existing
#: cache is considered stale.
_DUMP_PUBLISH_HOUR = 8
_DUMP_PUBLISH_MINUTE = 30

#: Columns Kite guarantees in the instruments dump. Ordered so our
#: Parquet files have a predictable schema; diagnostics tooling can
#: depend on this layout.
_SCHEMA_COLUMNS: tuple[str, ...] = (
    "instrument_token",
    "exchange_token",
    "tradingsymbol",
    "name",
    "last_price",
    "expiry",
    "strike",
    "tick_size",
    "lot_size",
    "instrument_type",
    "segment",
    "exchange",
)


def default_cache_dir() -> Path:
    """Return the on-disk directory for cached instruments dumps.

    Honours ``$ML4T_INDIA_INSTRUMENTS_DIR`` for tests and CI; default
    is ``~/.ml4t/india/instruments/``.
    """
    env_override = os.environ.get("ML4T_INDIA_INSTRUMENTS_DIR")
    if env_override:
        return Path(env_override).expanduser()
    return Path.home() / ".ml4t" / "india" / "instruments"


class _ClientProtocol(Protocol):
    """Minimal shape of a kiteconnect-compatible client used by this module.

    Defined here (rather than importing ``kiteconnect.KiteConnect``) so
    :class:`FakeKiteClient` and any future real client facade are both
    acceptable without touching this module.
    """

    def instruments(self, exchange: str | None = ...) -> list[dict[str, Any]]: ...


@dataclass(frozen=True, slots=True)
class InstrumentMeta:
    """A single row of Kite's instruments dump.

    Instances are :class:`frozen <dataclasses.dataclass>` so they are
    hashable and safe to share across threads; immutability also lets
    callers use them as dict keys when building lookup tables.
    """

    instrument_token: int
    exchange_token: int
    tradingsymbol: str
    name: str
    last_price: float
    expiry: dt.date | None
    strike: float
    tick_size: float
    lot_size: int
    instrument_type: str
    segment: str
    exchange: str


# ----------------------------------------------------------------------
# Cache
# ----------------------------------------------------------------------


class InstrumentsCache:
    """Loads, refreshes and queries the Kite instruments dump.

    Parameters
    ----------
    cache_dir:
        Directory where per-day ``YYYY-MM-DD.parquet`` files live.
        Defaults to :func:`default_cache_dir`.

    Notes
    -----
    The cache is INTENTIONALLY per-day and not a rolling single file.
    Having each day in its own file means:

    * A bad refresh never overwrites a known-good file.
    * Historical diagnostics ("what was the lot size last Tuesday?")
      are possible without an external archive.
    * Concurrent refreshers in separate processes do not collide on
      the target filename.
    """

    def __init__(self, cache_dir: Path | None = None) -> None:
        self._dir = (cache_dir or default_cache_dir()).expanduser()
        # Cache the most recently loaded frame in memory so repeat
        # resolves do not re-read the parquet every time.
        self._frame: pl.DataFrame | None = None
        self._loaded_day: dt.date | None = None

    # ---- cache lifecycle --------------------------------------------

    def cache_path(self, day: dt.date | None = None) -> Path:
        """Return the parquet path for ``day`` (default: today in IST)."""
        if day is None:
            day = dt.datetime.now(tz=_IST).date()
        return self._dir / f"{day.isoformat()}.parquet"

    def is_stale(self, now: dt.datetime | None = None) -> bool:
        """Return True if the cache should be refreshed.

        Refresh policy:

        * If no file for today exists -- stale.
        * If a file for today exists -- fresh (we already refreshed).
        * If only an older file exists AND the clock is past 08:30 IST
          today -- stale (Kite has published the new dump).
        * If only an older file exists AND the clock is before 08:30
          IST today -- fresh (yesterday's dump is still the latest
          Kite has published).
        """
        if now is None:
            now = dt.datetime.now(tz=_IST)
        now_ist = now.astimezone(_IST)
        today = now_ist.date()
        if self.cache_path(today).exists():
            return False
        # If it's still before Kite's daily publish boundary, we cannot
        # refresh usefully; yesterday's cache is fine.
        publish_boundary = dt.datetime.combine(
            today,
            dt.time(_DUMP_PUBLISH_HOUR, _DUMP_PUBLISH_MINUTE),
            tzinfo=_IST,
        )
        return now_ist >= publish_boundary

    def refresh(
        self,
        client: _ClientProtocol,
        day: dt.date | None = None,
    ) -> Path:
        """Fetch a fresh dump from ``client`` and persist as Parquet.

        Returns the path of the written Parquet file. Any existing file
        for the same day is overwritten (Parquet writes are
        self-contained so a crash mid-write leaves either the old file
        or the new file, never a partial).
        """
        rows = client.instruments()
        if not rows:
            raise DataIntegrityError(
                "kite instruments() returned an empty list; cannot refresh cache",
            )
        df = _normalize_instruments(rows)
        self._dir.mkdir(parents=True, exist_ok=True)
        target = self.cache_path(day)
        df.write_parquet(target)
        # Populate in-memory cache so the immediate-next resolve() is free.
        self._frame = df
        self._loaded_day = day or dt.datetime.now(tz=_IST).date()
        return target

    def load(self, day: dt.date | None = None) -> pl.DataFrame:
        """Return the cached frame for ``day`` (default: today in IST).

        Raises
        ------
        FileNotFoundError
            If no cache file exists for the requested day; call
            :meth:`refresh` first.
        """
        target_day = day or dt.datetime.now(tz=_IST).date()
        if self._frame is not None and self._loaded_day == target_day:
            return self._frame
        path = self.cache_path(target_day)
        if not path.exists():
            raise FileNotFoundError(
                f"no instruments cache for {target_day}; call refresh() first"
            )
        df = pl.read_parquet(path)
        self._frame = df
        self._loaded_day = target_day
        return df

    # ---- lookups ----------------------------------------------------

    def resolve(
        self,
        tradingsymbol: str,
        exchange: str | None = None,
        day: dt.date | None = None,
    ) -> InstrumentMeta:
        """Return the :class:`InstrumentMeta` for ``tradingsymbol``.

        Parameters
        ----------
        tradingsymbol:
            The user-facing symbol (e.g. ``"RELIANCE"`` or
            ``"NIFTY26APR25300CE"``). Compared case-sensitively --
            Kite's dump is already uppercase.
        exchange:
            Narrow the lookup to one exchange (``"NSE"``, ``"BSE"``,
            etc.). Useful for the duplicated-listing case where a
            symbol trades on both NSE and BSE.
        day:
            Historical lookup. Defaults to today (IST).

        Raises
        ------
        InstrumentNotFoundError
            When no row matches. The error carries a hint pointing at
            ``refresh()`` since the most common cause is a stale
            cache.
        """
        df = self.load(day)
        filtered = df.filter(pl.col("tradingsymbol") == tradingsymbol)
        if exchange is not None:
            filtered = filtered.filter(pl.col("exchange") == exchange)
        if filtered.height == 0:
            where = f" on {exchange}" if exchange else ""
            raise InstrumentNotFoundError(
                f"'{tradingsymbol}'{where} not found in instruments dump",
                hint="run `ml4t-india instruments refresh` to update the cache",
            )
        if filtered.height > 1:
            # Ambiguous symbol (e.g. RELIANCE trades on both NSE and BSE).
            # The caller must disambiguate with `exchange=`.
            exchanges = sorted(filtered["exchange"].to_list())
            raise InstrumentNotFoundError(
                f"'{tradingsymbol}' is ambiguous across {exchanges}; "
                "pass `exchange=` to disambiguate",
                hint=f"e.g. resolve('{tradingsymbol}', exchange='{exchanges[0]}')",
            )
        row = filtered.row(0, named=True)
        return _meta_from_row(row)

    def search(
        self,
        pattern: str,
        exchange: str | None = None,
        day: dt.date | None = None,
    ) -> list[InstrumentMeta]:
        """Case-insensitive substring search over ``tradingsymbol`` and ``name``.

        Returns at most 500 rows to keep the result addressable; callers
        that need everything should work directly with :meth:`load`.
        """
        df = self.load(day)
        p = pattern.upper()
        if not p:
            return []
        # Polars' str.contains uses regex by default; use literal=True to
        # avoid surprising the caller with regex meta-characters.
        matched = df.filter(
            pl.col("tradingsymbol").str.contains(p, literal=True)
            | pl.col("name").str.to_uppercase().str.contains(p, literal=True)
        )
        if exchange is not None:
            matched = matched.filter(pl.col("exchange") == exchange)
        return [
            _meta_from_row(r) for r in matched.head(500).iter_rows(named=True)
        ]


# ----------------------------------------------------------------------
# helpers
# ----------------------------------------------------------------------


def _normalize_instruments(rows: list[dict[str, Any]]) -> pl.DataFrame:
    """Coerce Kite's raw list-of-dicts into a canonical Polars frame.

    Kite returns native Python types -- ``int`` for tokens, ``float``
    for prices, ``datetime.date`` for expiry (empty string for cash
    equity). Normalising here means downstream readers of the cached
    Parquet do not need to re-parse those fields.
    """
    # Convert ``date`` objects to ISO strings for Parquet portability:
    # Polars DataFrame inference on mixed-type dicts is fragile, so be
    # explicit.
    clean: list[dict[str, Any]] = []
    for r in rows:
        expiry = r.get("expiry")
        if isinstance(expiry, dt.date):
            expiry_str = expiry.isoformat()
        elif isinstance(expiry, str):
            expiry_str = expiry or None
        else:
            expiry_str = None
        clean.append(
            {
                "instrument_token": int(r["instrument_token"]),
                "exchange_token": int(r.get("exchange_token", 0)),
                "tradingsymbol": str(r["tradingsymbol"]),
                "name": str(r.get("name", "")),
                "last_price": float(r.get("last_price", 0.0)),
                "expiry": expiry_str,
                "strike": float(r.get("strike", 0.0)),
                "tick_size": float(r.get("tick_size", 0.0)),
                "lot_size": int(r.get("lot_size", 0)),
                "instrument_type": str(r.get("instrument_type", "")),
                "segment": str(r.get("segment", "")),
                "exchange": str(r["exchange"]),
            }
        )
    df = pl.DataFrame(clean)
    # Reorder to the canonical column order.
    return df.select(list(_SCHEMA_COLUMNS))


def _meta_from_row(row: dict[str, Any]) -> InstrumentMeta:
    """Convert a Polars row-dict into an :class:`InstrumentMeta`."""
    raw_expiry = row.get("expiry")
    expiry: dt.date | None
    if raw_expiry in (None, ""):
        expiry = None
    elif isinstance(raw_expiry, dt.date):
        expiry = raw_expiry
    else:
        expiry = dt.date.fromisoformat(str(raw_expiry))
    return InstrumentMeta(
        instrument_token=int(row["instrument_token"]),
        exchange_token=int(row["exchange_token"]),
        tradingsymbol=str(row["tradingsymbol"]),
        name=str(row["name"]),
        last_price=float(row["last_price"]),
        expiry=expiry,
        strike=float(row["strike"]),
        tick_size=float(row["tick_size"]),
        lot_size=int(row["lot_size"]),
        instrument_type=str(row["instrument_type"]),
        segment=str(row["segment"]),
        exchange=str(row["exchange"]),
    )


__all__ = [
    "InstrumentMeta",
    "InstrumentsCache",
    "default_cache_dir",
]

"""Tests for :mod:`ml4t.india.kite.instruments`.

Covers:

* Path resolution (``default_cache_dir`` + env override).
* Staleness logic around Kite's 08:30 IST publish boundary.
* ``refresh`` persists to a per-day Parquet file; ``load`` reads it
  back; in-memory cache short-circuits the second read.
* ``resolve`` exact-match, ambiguous-multi-exchange, and not-found
  paths.
* ``search`` substring matches on tradingsymbol and name.

No real Kite calls are made: every test seeds a
:class:`FakeKiteClient` with a canned instruments list.
"""

from __future__ import annotations

import datetime as dt
import os
from pathlib import Path
from unittest.mock import patch

import pytest

from ml4t.india.core import InstrumentNotFoundError
from ml4t.india.core.exceptions import DataIntegrityError
from ml4t.india.kite import FakeKiteClient
from ml4t.india.kite.instruments import (
    InstrumentMeta,
    InstrumentsCache,
    default_cache_dir,
)

_IST = dt.timezone(dt.timedelta(hours=5, minutes=30), name="IST")

# ----------------------------------------------------------------------
# sample data
# ----------------------------------------------------------------------


def _sample_rows() -> list[dict[str, object]]:
    """Three realistic-ish rows: NSE equity, BSE equity, NFO option."""
    return [
        {
            "instrument_token": 738561,
            "exchange_token": 2885,
            "tradingsymbol": "RELIANCE",
            "name": "RELIANCE INDUSTRIES",
            "last_price": 2500.5,
            "expiry": "",
            "strike": 0.0,
            "tick_size": 0.05,
            "lot_size": 1,
            "instrument_type": "EQ",
            "segment": "NSE",
            "exchange": "NSE",
        },
        {
            "instrument_token": 140033796,
            "exchange_token": 547007,
            "tradingsymbol": "RELIANCE",
            "name": "RELIANCE INDUSTRIES",
            "last_price": 2500.0,
            "expiry": "",
            "strike": 0.0,
            "tick_size": 0.05,
            "lot_size": 1,
            "instrument_type": "EQ",
            "segment": "BSE",
            "exchange": "BSE",
        },
        {
            "instrument_token": 9671938,
            "exchange_token": 37781,
            "tradingsymbol": "NIFTY26APR25300CE",
            "name": "NIFTY",
            "last_price": 120.0,
            "expiry": dt.date(2026, 4, 30),
            "strike": 25300.0,
            "tick_size": 0.05,
            "lot_size": 50,
            "instrument_type": "CE",
            "segment": "NFO-OPT",
            "exchange": "NFO",
        },
    ]


# ----------------------------------------------------------------------
# default_cache_dir
# ----------------------------------------------------------------------


class TestDefaultCacheDir:
    def test_default_path(self) -> None:
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("ML4T_INDIA_INSTRUMENTS_DIR", None)
            p = default_cache_dir()
        assert p == Path.home() / ".ml4t" / "india" / "instruments"

    def test_env_override(self, tmp_path: Path) -> None:
        with patch.dict(os.environ, {"ML4T_INDIA_INSTRUMENTS_DIR": str(tmp_path)}):
            assert default_cache_dir() == tmp_path


# ----------------------------------------------------------------------
# Staleness
# ----------------------------------------------------------------------


class TestIsStale:
    def test_fresh_when_today_file_exists(self, tmp_path: Path) -> None:
        cache = InstrumentsCache(cache_dir=tmp_path)
        today = dt.datetime.now(tz=_IST).date()
        # Create today's parquet as an empty placeholder -- is_stale
        # only checks existence.
        cache.cache_path(today).touch()
        assert cache.is_stale() is False

    def test_stale_when_no_file_and_past_0830_ist(self, tmp_path: Path) -> None:
        cache = InstrumentsCache(cache_dir=tmp_path)
        now = dt.datetime(2026, 4, 21, 9, 0, tzinfo=_IST)
        assert cache.is_stale(now=now) is True

    def test_not_stale_when_no_file_and_before_0830_ist(
        self, tmp_path: Path
    ) -> None:
        """Kite has not yet published today's dump; yesterday's cache
        is considered the latest-available dump even if not on disk."""
        cache = InstrumentsCache(cache_dir=tmp_path)
        now = dt.datetime(2026, 4, 21, 7, 0, tzinfo=_IST)
        assert cache.is_stale(now=now) is False


# ----------------------------------------------------------------------
# refresh / load
# ----------------------------------------------------------------------


class TestRefreshAndLoad:
    def test_refresh_writes_parquet_for_today(self, tmp_path: Path) -> None:
        fake = FakeKiteClient()
        fake.set_instruments(_sample_rows())
        cache = InstrumentsCache(cache_dir=tmp_path)
        written = cache.refresh(fake)
        today = dt.datetime.now(tz=_IST).date()
        assert written == tmp_path / f"{today.isoformat()}.parquet"
        assert written.exists()

    def test_refresh_with_empty_dump_raises(self, tmp_path: Path) -> None:
        fake = FakeKiteClient()  # no instruments seeded -> empty list
        cache = InstrumentsCache(cache_dir=tmp_path)
        with pytest.raises(DataIntegrityError):
            cache.refresh(fake)

    def test_load_after_refresh_returns_same_frame(self, tmp_path: Path) -> None:
        fake = FakeKiteClient()
        fake.set_instruments(_sample_rows())
        cache = InstrumentsCache(cache_dir=tmp_path)
        cache.refresh(fake)
        df = cache.load()
        assert df.height == 3
        assert set(df.columns) == {
            "instrument_token", "exchange_token", "tradingsymbol",
            "name", "last_price", "expiry", "strike", "tick_size",
            "lot_size", "instrument_type", "segment", "exchange",
        }

    def test_load_without_refresh_raises(self, tmp_path: Path) -> None:
        cache = InstrumentsCache(cache_dir=tmp_path)
        with pytest.raises(FileNotFoundError, match="call refresh"):
            cache.load()


# ----------------------------------------------------------------------
# resolve
# ----------------------------------------------------------------------


class TestResolve:
    @pytest.fixture
    def cache(self, tmp_path: Path) -> InstrumentsCache:
        fake = FakeKiteClient()
        fake.set_instruments(_sample_rows())
        c = InstrumentsCache(cache_dir=tmp_path)
        c.refresh(fake)
        return c

    def test_exact_single_match(self, cache: InstrumentsCache) -> None:
        meta = cache.resolve("NIFTY26APR25300CE")
        assert isinstance(meta, InstrumentMeta)
        assert meta.instrument_token == 9671938
        assert meta.lot_size == 50
        assert meta.expiry == dt.date(2026, 4, 30)
        assert meta.instrument_type == "CE"

    def test_ambiguous_match_raises_with_hint(
        self, cache: InstrumentsCache
    ) -> None:
        """RELIANCE trades on both NSE and BSE -- caller must disambiguate."""
        with pytest.raises(InstrumentNotFoundError) as exc_info:
            cache.resolve("RELIANCE")
        assert "ambiguous" in str(exc_info.value).lower()

    def test_ambiguous_resolves_with_exchange(self, cache: InstrumentsCache) -> None:
        meta = cache.resolve("RELIANCE", exchange="NSE")
        assert meta.exchange == "NSE"
        assert meta.instrument_token == 738561

    def test_missing_symbol_raises(self, cache: InstrumentsCache) -> None:
        with pytest.raises(InstrumentNotFoundError):
            cache.resolve("DOES_NOT_EXIST")

    def test_missing_on_wrong_exchange_raises(self, cache: InstrumentsCache) -> None:
        with pytest.raises(InstrumentNotFoundError):
            cache.resolve("RELIANCE", exchange="MCX")


# ----------------------------------------------------------------------
# search
# ----------------------------------------------------------------------


class TestSearch:
    @pytest.fixture
    def cache(self, tmp_path: Path) -> InstrumentsCache:
        fake = FakeKiteClient()
        fake.set_instruments(_sample_rows())
        c = InstrumentsCache(cache_dir=tmp_path)
        c.refresh(fake)
        return c

    def test_substring_on_tradingsymbol(self, cache: InstrumentsCache) -> None:
        hits = cache.search("NIFTY")
        assert len(hits) == 1
        assert hits[0].tradingsymbol == "NIFTY26APR25300CE"

    def test_substring_on_name(self, cache: InstrumentsCache) -> None:
        """'INDUSTRIES' is in RELIANCE's name; should match both NSE+BSE rows."""
        hits = cache.search("INDUSTRIES")
        symbols = {h.tradingsymbol for h in hits}
        assert symbols == {"RELIANCE"}  # two rows, same tradingsymbol
        assert len(hits) == 2

    def test_exchange_filter(self, cache: InstrumentsCache) -> None:
        hits = cache.search("RELIANCE", exchange="BSE")
        assert len(hits) == 1
        assert hits[0].exchange == "BSE"

    def test_empty_pattern_returns_empty(self, cache: InstrumentsCache) -> None:
        assert cache.search("") == []

    def test_search_is_case_insensitive(self, cache: InstrumentsCache) -> None:
        upper = cache.search("RELIANCE")
        lower = cache.search("reliance")
        assert len(upper) == len(lower) == 2

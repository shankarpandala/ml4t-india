"""Tests for :class:`ml4t.india.data.kite.KiteProvider`.

No network: seeds :class:`FakeKiteClient` + in-memory
:class:`InstrumentsCache`.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from ml4t.india.core import Exchange, InstrumentNotFoundError
from ml4t.india.data import KiteProvider
from ml4t.india.kite import FakeKiteClient
from ml4t.india.kite.client import KiteClient
from ml4t.india.kite.instruments import InstrumentsCache
from ml4t.india.kite.rate_limit import KiteRateLimiter


def _fast_client(fake: FakeKiteClient) -> KiteClient:
    limiter = KiteRateLimiter(
        limits={
            "quote": 1000.0,
            "historical": 1000.0,
            "orders": 1000.0,
            "other": 1000.0,
        },
        global_rate=1000.0,
    )
    return KiteClient(fake, rate_limiter=limiter)


@pytest.fixture
def provider(tmp_path: Path) -> tuple[KiteProvider, FakeKiteClient]:
    fake = FakeKiteClient()
    fake.set_instruments(
        [
            {
                "instrument_token": 738561,
                "exchange_token": 2885,
                "tradingsymbol": "RELIANCE",
                "name": "RELIANCE INDUSTRIES",
                "last_price": 2500.0,
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
        ]
    )
    cache = InstrumentsCache(cache_dir=tmp_path)
    cache.refresh(fake)
    client = _fast_client(fake)
    return KiteProvider(client=client, instruments=cache), fake


# ---------- inheritance contract ----------


class TestInheritance:
    def test_extends_indian_ohlcv_provider(self) -> None:
        from ml4t.india.data import IndianOHLCVProvider

        assert issubclass(KiteProvider, IndianOHLCVProvider)

    def test_name_is_kite(self, provider: tuple[KiteProvider, FakeKiteClient]) -> None:
        p, _ = provider
        assert p.name == "kite"

    def test_supported_exchanges_cover_indian_venues(self) -> None:
        assert Exchange.NSE in KiteProvider.SUPPORTED_EXCHANGES
        assert Exchange.NFO in KiteProvider.SUPPORTED_EXCHANGES
        assert Exchange.MCX in KiteProvider.SUPPORTED_EXCHANGES


# ---------- fetch ----------


class TestFetchOHLCV:
    def test_bare_symbol_resolves_via_default_exchange(
        self, provider: tuple[KiteProvider, FakeKiteClient]
    ) -> None:
        p, fake = provider
        fake.set_historical_data(
            "738561",
            [
                {
                    "date": "2024-01-01T09:15:00+05:30",
                    "open": 100.0,
                    "high": 101.0,
                    "low": 99.0,
                    "close": 100.5,
                    "volume": 1234.0,
                },
            ],
        )
        df = p.fetch_ohlcv("RELIANCE", "2024-01-01", "2024-01-02", "daily")
        assert df.height == 1
        assert df["symbol"][0] == "RELIANCE"
        assert df["open"][0] == 100.0
        assert df["volume"][0] == 1234.0

    def test_exchange_prefix_pins_venue(
        self, provider: tuple[KiteProvider, FakeKiteClient]
    ) -> None:
        """`BSE:RELIANCE` must resolve to BSE instrument_token, not NSE."""
        p, fake = provider
        fake.set_historical_data(
            "140033796",
            [
                {
                    "date": "2024-01-01T09:15:00+05:30",
                    "open": 200.0,
                    "high": 201.0,
                    "low": 199.0,
                    "close": 200.5,
                    "volume": 100.0,
                },
            ],
        )
        df = p.fetch_ohlcv("BSE:RELIANCE", "2024-01-01", "2024-01-02", "daily")
        assert df.height == 1
        assert df["open"][0] == 200.0

    def test_unknown_symbol_raises(
        self, provider: tuple[KiteProvider, FakeKiteClient]
    ) -> None:
        p, _ = provider
        with pytest.raises(InstrumentNotFoundError):
            p.fetch_ohlcv("DOES_NOT_EXIST", "2024-01-01", "2024-01-02", "daily")

    def test_frequency_map_translates_to_kite_interval(
        self, provider: tuple[KiteProvider, FakeKiteClient]
    ) -> None:
        """'5min' and 'daily' map to '5minute' and 'day' on the wire."""
        p, fake = provider
        fake.set_historical_data(
            "738561",
            [
                {
                    "date": "2024-01-01T09:15:00+05:30",
                    "open": 1.0,
                    "high": 1.0,
                    "low": 1.0,
                    "close": 1.0,
                    "volume": 0.0,
                },
            ],
        )
        p.fetch_ohlcv("RELIANCE", "2024-01-01", "2024-01-02", "5min")
        assert fake.calls[-1].args[3] == "5minute"
        p.fetch_ohlcv("RELIANCE", "2024-01-01", "2024-01-02", "daily")
        assert fake.calls[-1].args[3] == "day"

    def test_empty_response_returns_empty_frame(
        self, provider: tuple[KiteProvider, FakeKiteClient]
    ) -> None:
        p, fake = provider
        fake.set_historical_data("738561", [])
        df = p.fetch_ohlcv("RELIANCE", "2024-01-01", "2024-01-02", "daily")
        assert df.height == 0
        assert set(df.columns) == {
            "timestamp", "symbol", "open", "high", "low", "close", "volume",
        }

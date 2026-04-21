"""Tests for :class:`ml4t.india.data.kite_async.KiteAsyncProvider`."""

from __future__ import annotations

from pathlib import Path

import pytest

from ml4t.india.data import KiteAsyncProvider, KiteProvider
from ml4t.india.kite import FakeKiteClient
from ml4t.india.kite.client import KiteClient
from ml4t.india.kite.instruments import InstrumentsCache
from ml4t.india.kite.rate_limit import KiteRateLimiter


def _fast_limiter() -> KiteRateLimiter:
    return KiteRateLimiter(
        limits={
            "quote": 1000.0,
            "historical": 1000.0,
            "orders": 1000.0,
            "other": 1000.0,
        },
        global_rate=1000.0,
    )


@pytest.fixture
def async_provider(tmp_path: Path) -> KiteAsyncProvider:
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
        ]
    )
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
    cache = InstrumentsCache(cache_dir=tmp_path)
    cache.refresh(fake)
    sync = KiteProvider(
        client=KiteClient(fake, rate_limiter=_fast_limiter()),
        instruments=cache,
    )
    return KiteAsyncProvider(sync)


class TestAsync:
    def test_name_delegates_to_sync(
        self, async_provider: KiteAsyncProvider
    ) -> None:
        assert async_provider.name == "kite"

    @pytest.mark.asyncio
    async def test_fetch_ohlcv_async_returns_frame(
        self, async_provider: KiteAsyncProvider
    ) -> None:
        df = await async_provider.fetch_ohlcv_async(
            "RELIANCE", "2024-01-01", "2024-01-02", "daily"
        )
        assert df.height == 1
        assert df["symbol"][0] == "RELIANCE"
        assert df["open"][0] == 100.0

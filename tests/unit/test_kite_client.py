"""Tests for :mod:`ml4t.india.kite.client`."""

from __future__ import annotations

import asyncio

import pytest
from kiteconnect import exceptions as kexc

from ml4t.india.core.exceptions import (
    IndiaError,
    InvalidInputError,
    OrderError,
    TokenExpiredError,
)
from ml4t.india.kite import FakeKiteClient
from ml4t.india.kite.client import (
    _CATEGORY_FOR,
    AsyncKiteClient,
    KiteClient,
)
from ml4t.india.kite.rate_limit import KiteRateLimiter, TokenBucket


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


class TestDispatch:
    def test_profile_forwards_to_sdk(self) -> None:
        fake = FakeKiteClient()
        client = KiteClient(fake, rate_limiter=_fast_limiter())
        assert client.profile()["broker"] == "ZERODHA"
        assert [c.method for c in fake.calls] == ["profile"]

    def test_historical_data_forwards_args(self) -> None:
        fake = FakeKiteClient()
        fake.set_historical_data("738561", [{"date": "2024-01-01"}])
        client = KiteClient(fake, rate_limiter=_fast_limiter())
        rows = client.historical_data(738561, "2024-01-01", "2024-01-31", "day")
        assert rows == [{"date": "2024-01-01"}]

    def test_place_order_forwards_kwargs(self) -> None:
        fake = FakeKiteClient()
        client = KiteClient(fake, rate_limiter=_fast_limiter())
        oid = client.place_order(
            "regular",
            tradingsymbol="RELIANCE",
            exchange="NSE",
            transaction_type="BUY",
            quantity=1,
            product="CNC",
            order_type="MARKET",
            tag="unit-test",
        )
        assert oid.startswith("FAKE-")
        assert fake.calls[-1].kwargs["tag"] == "unit-test"

    def test_cancel_order_forwards_positional(self) -> None:
        fake = FakeKiteClient()
        client = KiteClient(fake, rate_limiter=_fast_limiter())
        oid = client.place_order(
            "regular",
            tradingsymbol="INFY",
            exchange="NSE",
            transaction_type="BUY",
            quantity=1,
            product="CNC",
            order_type="MARKET",
        )
        client.cancel_order("regular", oid)
        assert fake.calls[-1].method == "cancel_order"
        assert fake.calls[-1].args == ("regular", oid)


class TestCategoryMapping:
    @pytest.mark.parametrize(
        ("method", "category"),
        [
            ("quote", "quote"),
            ("ltp", "quote"),
            ("ohlc", "quote"),
            ("historical_data", "historical"),
            ("place_order", "orders"),
            ("modify_order", "orders"),
            ("cancel_order", "orders"),
        ],
    )
    def test_mapping_pins_published_kite_ceilings(
        self, method: str, category: str
    ) -> None:
        assert _CATEGORY_FOR[method] == category

    def test_unknown_method_falls_through_to_other(self) -> None:
        assert _CATEGORY_FOR.get("some_future_method") is None


class TestRateLimitIntegration:
    def test_each_call_consumes_one_global_token(self) -> None:
        """Drain the global bucket; next acquire times out."""
        limiter = KiteRateLimiter(
            limits={
                "quote": 1000.0,
                "historical": 1000.0,
                "orders": 1000.0,
                "other": 1000.0,
            },
            global_rate=1.0,
        )
        # capacity=2 lets two calls pass; rate=1/s means refill won't
        # happen inside the 50ms test timeout.
        limiter._global = TokenBucket(rate=1.0, capacity=2.0)  # noqa: SLF001

        client = KiteClient(FakeKiteClient(), rate_limiter=limiter)
        client.profile()
        client.profile()
        with pytest.raises(TimeoutError):
            limiter.acquire("other", timeout=0.05)

    def test_quote_category_not_binding_at_fast_rates(self) -> None:
        limiter = KiteRateLimiter(
            limits={
                "quote": 200.0,
                "historical": 1000.0,
                "orders": 1000.0,
                "other": 1000.0,
            },
            global_rate=1000.0,
        )
        client = KiteClient(FakeKiteClient(), rate_limiter=limiter)
        client.ltp(["NSE:RELIANCE"])
        client.ltp(["NSE:RELIANCE"])


class TestExceptionTranslation:
    def test_token_exception_becomes_token_expired(self) -> None:
        fake = FakeKiteClient()
        fake.set_next_error(kexc.TokenException("token bad"))
        client = KiteClient(fake, rate_limiter=_fast_limiter())
        with pytest.raises(TokenExpiredError):
            client.profile()

    def test_input_exception_becomes_invalid_input(self) -> None:
        fake = FakeKiteClient()
        fake.set_next_error(kexc.InputException("bad arg"))
        client = KiteClient(fake, rate_limiter=_fast_limiter())
        with pytest.raises(InvalidInputError):
            client.profile()

    def test_order_exception_becomes_order_error(self) -> None:
        fake = FakeKiteClient()
        fake.set_next_error(kexc.OrderException("rejected"))
        client = KiteClient(fake, rate_limiter=_fast_limiter())
        with pytest.raises(OrderError):
            client.place_order(
                "regular",
                tradingsymbol="X",
                exchange="NSE",
                transaction_type="BUY",
                quantity=1,
                product="CNC",
                order_type="MARKET",
            )

    def test_translated_error_chains_to_original(self) -> None:
        fake = FakeKiteClient()
        original = kexc.TokenException("expired")
        fake.set_next_error(original)
        client = KiteClient(fake, rate_limiter=_fast_limiter())
        with pytest.raises(IndiaError) as exc_info:
            client.profile()
        assert exc_info.value.__cause__ is original
        assert exc_info.value.cause is original


class TestAsyncKiteClient:
    @pytest.mark.asyncio
    async def test_profile_is_awaitable(self) -> None:
        aclient = AsyncKiteClient(
            KiteClient(FakeKiteClient(), rate_limiter=_fast_limiter())
        )
        assert (await aclient.profile())["broker"] == "ZERODHA"

    @pytest.mark.asyncio
    async def test_exceptions_propagate_translated(self) -> None:
        fake = FakeKiteClient()
        fake.set_next_error(kexc.TokenException("expired"))
        aclient = AsyncKiteClient(
            KiteClient(fake, rate_limiter=_fast_limiter())
        )
        with pytest.raises(TokenExpiredError):
            await aclient.profile()

    @pytest.mark.asyncio
    async def test_place_order_kwargs_forwarded(self) -> None:
        fake = FakeKiteClient()
        aclient = AsyncKiteClient(
            KiteClient(fake, rate_limiter=_fast_limiter())
        )
        oid = await aclient.place_order(
            "regular",
            tradingsymbol="TCS",
            exchange="NSE",
            transaction_type="SELL",
            quantity=2,
            product="MIS",
            order_type="LIMIT",
            price=4000.0,
        )
        assert oid.startswith("FAKE-")
        assert fake.calls[-1].kwargs["price"] == 4000.0

    @pytest.mark.asyncio
    async def test_concurrent_calls_all_land(self) -> None:
        fake = FakeKiteClient()
        aclient = AsyncKiteClient(
            KiteClient(fake, rate_limiter=_fast_limiter())
        )
        results = await asyncio.gather(*[aclient.profile() for _ in range(10)])
        assert len(results) == 10
        assert all(r["broker"] == "ZERODHA" for r in results)
        assert len([c for c in fake.calls if c.method == "profile"]) == 10

"""Tests for :class:`ml4t.india.live.kite_broker.KiteBroker`.

Uses :class:`FakeKiteClient` + real :class:`AsyncKiteClient` so the
rate-limit / translation layer actually runs; only the SDK is faked.
"""

from __future__ import annotations

import pytest
from ml4t.backtest.types import Order, OrderSide, OrderStatus, OrderType, Position

from ml4t.india.core.exceptions import InvalidInputError
from ml4t.india.kite.client import AsyncKiteClient, KiteClient
from ml4t.india.kite.fake import FakeKiteClient
from ml4t.india.kite.rate_limit import KiteRateLimiter
from ml4t.india.live.base import IndianBrokerBase
from ml4t.india.live.kite_broker import KiteBroker

# ---- fixtures ----------------------------------------------------------


def _fast_limiter() -> KiteRateLimiter:
    """Limits loose enough to never block in unit tests."""
    return KiteRateLimiter(
        limits={
            "quote": 500.0,
            "historical": 500.0,
            "orders": 500.0,
            "other": 500.0,
        },
        global_rate=500.0,
    )


@pytest.fixture
def fake_sdk() -> FakeKiteClient:
    return FakeKiteClient(api_key="test", access_token="tok")


@pytest.fixture
def broker(fake_sdk: FakeKiteClient) -> KiteBroker:
    sync = KiteClient(sdk=fake_sdk, rate_limiter=_fast_limiter())
    return KiteBroker(AsyncKiteClient(sync))


# ---- inheritance ------------------------------------------------------


class TestInheritance:
    def test_is_indian_broker_base(self, broker: KiteBroker) -> None:
        assert isinstance(broker, IndianBrokerBase)

    def test_all_abstract_methods_concrete(self) -> None:
        assert not getattr(KiteBroker, "__abstractmethods__", set())


# ---- connection -------------------------------------------------------


class TestConnection:
    async def test_connect_probes_profile(
        self, broker: KiteBroker, fake_sdk: FakeKiteClient
    ) -> None:
        assert await broker.is_connected_async() is False
        await broker.connect()
        assert await broker.is_connected_async() is True
        assert any(c.method == "profile" for c in fake_sdk.calls)

    async def test_connect_is_idempotent(
        self, broker: KiteBroker, fake_sdk: FakeKiteClient
    ) -> None:
        await broker.connect()
        await broker.connect()
        profile_calls = [c for c in fake_sdk.calls if c.method == "profile"]
        assert len(profile_calls) == 1

    async def test_disconnect_flips_flag(self, broker: KiteBroker) -> None:
        await broker.connect()
        await broker.disconnect()
        assert await broker.is_connected_async() is False


# ---- account ----------------------------------------------------------


class TestAccount:
    async def test_get_cash_reads_equity_available(
        self, broker: KiteBroker, fake_sdk: FakeKiteClient
    ) -> None:
        fake_sdk.set_margins({"equity": {"available": {"cash": 123456.78}}})
        assert await broker.get_cash_async() == pytest.approx(123456.78)

    async def test_get_cash_defaults_to_zero_if_missing(self, broker: KiteBroker) -> None:
        # No margins seeded -> empty dict -> zero.
        assert await broker.get_cash_async() == 0.0

    async def test_account_value_is_cash_plus_mtm(
        self, broker: KiteBroker, fake_sdk: FakeKiteClient
    ) -> None:
        fake_sdk.set_margins({"equity": {"available": {"cash": 100000.0}}})
        fake_sdk._positions = [
            {
                "exchange": "NSE",
                "tradingsymbol": "RELIANCE",
                "quantity": 10,
                "average_price": 2500.0,
                "last_price": 2600.0,
                "multiplier": 1,
            }
        ]
        # cash + qty * mult * last_price
        expected = 100000.0 + 10 * 1 * 2600.0
        assert await broker.get_account_value_async() == pytest.approx(expected)


# ---- positions --------------------------------------------------------


class TestPositions:
    async def test_get_positions_returns_non_zero_only(
        self, broker: KiteBroker, fake_sdk: FakeKiteClient
    ) -> None:
        fake_sdk._positions = [
            {
                "exchange": "NSE",
                "tradingsymbol": "RELIANCE",
                "quantity": 10,
                "average_price": 2500.0,
                "last_price": 2600.0,
                "multiplier": 1,
            },
            {
                "exchange": "NSE",
                "tradingsymbol": "TCS",
                "quantity": 0,
                "average_price": 3800.0,
                "last_price": 3850.0,
                "multiplier": 1,
            },
        ]
        positions = await broker.get_positions_async()
        assert set(positions.keys()) == {"NSE:RELIANCE"}
        pos = positions["NSE:RELIANCE"]
        assert isinstance(pos, Position)
        assert pos.quantity == 10

    async def test_get_position_single(self, broker: KiteBroker, fake_sdk: FakeKiteClient) -> None:
        fake_sdk._positions = [
            {
                "exchange": "NSE",
                "tradingsymbol": "INFY",
                "quantity": -5,
                "average_price": 1500.0,
                "last_price": 1520.0,
                "multiplier": 1,
            }
        ]
        pos = await broker.get_position_async("NSE:INFY")
        assert pos is not None
        assert pos.quantity == -5

    async def test_get_position_missing_returns_none(self, broker: KiteBroker) -> None:
        assert await broker.get_position_async("NSE:NONE") is None

    async def test_close_position_flat_returns_none(self, broker: KiteBroker) -> None:
        assert await broker.close_position_async("NSE:NONE") is None

    async def test_close_long_position_emits_sell(
        self, broker: KiteBroker, fake_sdk: FakeKiteClient
    ) -> None:
        fake_sdk._positions = [
            {
                "exchange": "NSE",
                "tradingsymbol": "RELIANCE",
                "quantity": 10,
                "average_price": 2500.0,
                "last_price": 2600.0,
                "multiplier": 1,
            }
        ]
        order = await broker.close_position_async("NSE:RELIANCE")
        assert order is not None
        assert order.side == OrderSide.SELL
        assert order.quantity == 10

    async def test_close_short_position_emits_buy(
        self, broker: KiteBroker, fake_sdk: FakeKiteClient
    ) -> None:
        fake_sdk._positions = [
            {
                "exchange": "NFO",
                "tradingsymbol": "NIFTY26APRFUT",
                "quantity": -75,
                "average_price": 25000.0,
                "last_price": 24900.0,
                "multiplier": 1,
            }
        ]
        order = await broker.close_position_async("NFO:NIFTY26APRFUT")
        assert order is not None
        assert order.side == OrderSide.BUY
        assert order.quantity == 75


# ---- orders -----------------------------------------------------------


class TestSubmitOrder:
    async def test_market_order_buy(self, broker: KiteBroker, fake_sdk: FakeKiteClient) -> None:
        order = await broker.submit_order_async(asset="NSE:RELIANCE", quantity=10)
        assert isinstance(order, Order)
        assert order.asset == "NSE:RELIANCE"
        assert order.side == OrderSide.BUY
        assert order.quantity == 10
        assert order.order_type == OrderType.MARKET
        assert order.order_id.startswith("FAKE-")

        place = [c for c in fake_sdk.calls if c.method == "place_order"]
        assert len(place) == 1
        kwargs = place[0].kwargs
        assert kwargs["exchange"] == "NSE"
        assert kwargs["tradingsymbol"] == "RELIANCE"
        assert kwargs["transaction_type"] == "BUY"
        assert kwargs["product"] == "CNC"  # default for NSE equity
        assert kwargs["order_type"] == "MARKET"
        assert kwargs["quantity"] == 10

    async def test_negative_quantity_implies_sell(
        self, broker: KiteBroker, fake_sdk: FakeKiteClient
    ) -> None:
        order = await broker.submit_order_async(asset="NSE:RELIANCE", quantity=-10)
        assert order.side == OrderSide.SELL
        assert order.quantity == 10  # absolute
        place = [c for c in fake_sdk.calls if c.method == "place_order"]
        assert place[0].kwargs["transaction_type"] == "SELL"

    async def test_limit_order_sends_price(
        self, broker: KiteBroker, fake_sdk: FakeKiteClient
    ) -> None:
        await broker.submit_order_async(
            asset="NSE:RELIANCE",
            quantity=10,
            order_type=OrderType.LIMIT,
            limit_price=2500.0,
        )
        place = [c for c in fake_sdk.calls if c.method == "place_order"]
        assert place[0].kwargs["price"] == 2500.0
        assert place[0].kwargs["order_type"] == "LIMIT"

    async def test_stop_market_order(self, broker: KiteBroker, fake_sdk: FakeKiteClient) -> None:
        await broker.submit_order_async(
            asset="NSE:RELIANCE",
            quantity=10,
            order_type=OrderType.STOP,
            stop_price=2550.0,
        )
        place = [c for c in fake_sdk.calls if c.method == "place_order"]
        assert place[0].kwargs["trigger_price"] == 2550.0
        assert place[0].kwargs["order_type"] == "SL-M"

    async def test_futures_defaults_to_nrml(
        self, broker: KiteBroker, fake_sdk: FakeKiteClient
    ) -> None:
        await broker.submit_order_async(asset="NFO:NIFTY26APRFUT", quantity=75)
        place = [c for c in fake_sdk.calls if c.method == "place_order"]
        assert place[0].kwargs["product"] == "NRML"

    async def test_trailing_stop_rejected(self, broker: KiteBroker) -> None:
        with pytest.raises(InvalidInputError, match="no native order-type"):
            await broker.submit_order_async(
                asset="NSE:RELIANCE",
                quantity=10,
                order_type=OrderType.TRAILING_STOP,
            )

    async def test_zero_quantity_rejected(self, broker: KiteBroker) -> None:
        with pytest.raises(InvalidInputError, match="nonzero"):
            await broker.submit_order_async(asset="NSE:RELIANCE", quantity=0)

    async def test_bare_symbol_rejected(self, broker: KiteBroker) -> None:
        with pytest.raises(InvalidInputError, match="EXCHANGE:SYMBOL"):
            await broker.submit_order_async(asset="RELIANCE", quantity=10)

    async def test_custom_product_override(
        self, broker: KiteBroker, fake_sdk: FakeKiteClient
    ) -> None:
        await broker.submit_order_async(asset="NSE:RELIANCE", quantity=10, product="MIS")
        place = [c for c in fake_sdk.calls if c.method == "place_order"]
        assert place[0].kwargs["product"] == "MIS"


class TestCancelOrder:
    async def test_cancel_echoes_true(self, broker: KiteBroker, fake_sdk: FakeKiteClient) -> None:
        order = await broker.submit_order_async(asset="NSE:RELIANCE", quantity=10)
        assert await broker.cancel_order_async(order.order_id) is True
        cancels = [c for c in fake_sdk.calls if c.method == "cancel_order"]
        assert cancels[0].args == ("regular", order.order_id)


class TestGetPendingOrders:
    async def test_filters_out_terminal(self, broker: KiteBroker, fake_sdk: FakeKiteClient) -> None:
        # Two completed (auto-filled by fake), then we add one pending manually.
        await broker.submit_order_async(asset="NSE:RELIANCE", quantity=10)
        fake_sdk._orders.append(
            {
                "order_id": "PEND-1",
                "status": "OPEN",
                "exchange": "NSE",
                "tradingsymbol": "INFY",
                "transaction_type": "BUY",
                "quantity": 5,
                "filled_quantity": 0,
                "order_type": "LIMIT",
                "price": 1500.0,
                "average_price": 0,
            }
        )
        pending = await broker.get_pending_orders_async()
        assert len(pending) == 1
        assert pending[0].order_id == "PEND-1"
        assert pending[0].status == OrderStatus.PENDING
        assert pending[0].order_type == OrderType.LIMIT

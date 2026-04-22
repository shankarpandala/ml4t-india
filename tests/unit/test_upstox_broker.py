"""Tests for :class:`ml4t.india.live.upstox_broker.UpstoxBroker`."""

from __future__ import annotations

from typing import Any

import pytest
from ml4t.backtest.types import OrderSide, OrderStatus, OrderType

from ml4t.india.core.exceptions import InvalidInputError
from ml4t.india.live.base import IndianBrokerBase
from ml4t.india.live.upstox_broker import UpstoxBroker


class FakeUpstoxClient:
    """Minimal fake of the Upstox SDK surface UpstoxBroker depends on."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, tuple[Any, ...], dict[str, Any]]] = []
        self._funds: dict[str, Any] = {"equity": {"available_margin": 123456.78}}
        self._positions: list[dict[str, Any]] = []
        self._orders: list[dict[str, Any]] = []
        self._next_order_id: int = 1

    def _record(self, method: str, *args: Any, **kwargs: Any) -> None:
        self.calls.append((method, args, kwargs))

    def get_profile(self) -> dict[str, Any]:
        self._record("get_profile")
        return {"user_id": "abc"}

    def get_funds_and_margin(self, segment: str = "equity") -> dict[str, Any]:
        self._record("get_funds_and_margin", segment=segment)
        return self._funds

    def get_positions(self) -> list[dict[str, Any]]:
        self._record("get_positions")
        return list(self._positions)

    def place_order(self, **kwargs: Any) -> str:
        self._record("place_order", **kwargs)
        order_id = f"UPSTOX-{self._next_order_id:06d}"
        self._next_order_id += 1
        self._orders.append(
            {
                "order_id": order_id,
                "status": "open",
                "exchange": kwargs.get("exchange", "NSE"),
                "tradingsymbol": kwargs.get("tradingsymbol", ""),
                "transaction_type": kwargs.get("transaction_type", "BUY"),
                "quantity": kwargs.get("quantity", 0),
                "filled_quantity": 0,
            }
        )
        return order_id

    def cancel_order(self, order_id: str, **kwargs: Any) -> str:
        self._record("cancel_order", order_id, **kwargs)
        for o in self._orders:
            if o["order_id"] == order_id:
                o["status"] = "cancelled"
        return order_id

    def get_order_book(self) -> list[dict[str, Any]]:
        self._record("get_order_book")
        return [dict(o) for o in self._orders]


@pytest.fixture
def sdk() -> FakeUpstoxClient:
    return FakeUpstoxClient()


@pytest.fixture
def broker(sdk: FakeUpstoxClient) -> UpstoxBroker:
    return UpstoxBroker(sdk)


class TestInheritance:
    def test_is_indian_broker_base(self, broker: UpstoxBroker) -> None:
        assert isinstance(broker, IndianBrokerBase)

    def test_no_abstract_methods(self) -> None:
        assert not getattr(UpstoxBroker, "__abstractmethods__", set())


class TestConnection:
    async def test_connect_probes_profile(
        self, broker: UpstoxBroker, sdk: FakeUpstoxClient
    ) -> None:
        await broker.connect()
        assert any(c[0] == "get_profile" for c in sdk.calls)
        assert await broker.is_connected_async() is True

    async def test_connect_idempotent(self, broker: UpstoxBroker, sdk: FakeUpstoxClient) -> None:
        await broker.connect()
        await broker.connect()
        assert len([c for c in sdk.calls if c[0] == "get_profile"]) == 1


class TestAccount:
    async def test_get_cash(self, broker: UpstoxBroker) -> None:
        assert await broker.get_cash_async() == pytest.approx(123456.78)

    async def test_account_value_cash_plus_mtm(
        self, broker: UpstoxBroker, sdk: FakeUpstoxClient
    ) -> None:
        sdk._funds = {"equity": {"available_margin": 100000.0}}
        sdk._positions = [
            {
                "exchange": "NSE",
                "tradingsymbol": "RELIANCE",
                "quantity": 10,
                "average_price": 2500.0,
                "last_price": 2600.0,
                "multiplier": 1,
            }
        ]
        assert await broker.get_account_value_async() == pytest.approx(100000.0 + 10 * 2600.0)


class TestPositions:
    async def test_get_positions_filters_zero(
        self, broker: UpstoxBroker, sdk: FakeUpstoxClient
    ) -> None:
        sdk._positions = [
            {
                "exchange": "NSE",
                "tradingsymbol": "A",
                "quantity": 5,
                "average_price": 100,
                "last_price": 110,
                "multiplier": 1,
            },
            {
                "exchange": "NSE",
                "tradingsymbol": "B",
                "quantity": 0,
                "average_price": 200,
                "last_price": 210,
                "multiplier": 1,
            },
        ]
        positions = await broker.get_positions_async()
        assert set(positions.keys()) == {"NSE:A"}


class TestSubmitOrder:
    async def test_market_buy(self, broker: UpstoxBroker, sdk: FakeUpstoxClient) -> None:
        order = await broker.submit_order_async(asset="NSE:RELIANCE", quantity=10)
        assert order.side == OrderSide.BUY
        place_kwargs = next(c for c in sdk.calls if c[0] == "place_order")[2]
        assert place_kwargs["exchange"] == "NSE"
        assert place_kwargs["tradingsymbol"] == "RELIANCE"
        assert place_kwargs["transaction_type"] == "BUY"
        assert place_kwargs["order_type"] == "MARKET"

    async def test_limit_sell_negative_qty(
        self, broker: UpstoxBroker, sdk: FakeUpstoxClient
    ) -> None:
        order = await broker.submit_order_async(
            asset="NSE:TCS", quantity=-5, order_type=OrderType.LIMIT, limit_price=3800.0
        )
        assert order.side == OrderSide.SELL
        place_kwargs = next(c for c in sdk.calls if c[0] == "place_order")[2]
        assert place_kwargs["transaction_type"] == "SELL"
        assert place_kwargs["price"] == 3800.0

    async def test_stop_order_sets_trigger(
        self, broker: UpstoxBroker, sdk: FakeUpstoxClient
    ) -> None:
        await broker.submit_order_async(
            asset="NSE:RELIANCE", quantity=10, order_type=OrderType.STOP, stop_price=2450.0
        )
        place_kwargs = next(c for c in sdk.calls if c[0] == "place_order")[2]
        assert place_kwargs["trigger_price"] == 2450.0
        assert place_kwargs["order_type"] == "SL-M"

    async def test_trailing_stop_rejected(self, broker: UpstoxBroker) -> None:
        with pytest.raises(InvalidInputError, match="order_type"):
            await broker.submit_order_async(
                asset="NSE:RELIANCE", quantity=10, order_type=OrderType.TRAILING_STOP
            )

    async def test_bare_symbol_rejected(self, broker: UpstoxBroker) -> None:
        with pytest.raises(InvalidInputError, match="EXCHANGE:SYMBOL"):
            await broker.submit_order_async(asset="RELIANCE", quantity=10)

    async def test_zero_quantity_rejected(self, broker: UpstoxBroker) -> None:
        with pytest.raises(InvalidInputError, match="nonzero"):
            await broker.submit_order_async(asset="NSE:RELIANCE", quantity=0)


class TestCancel:
    async def test_cancel_echoes_true(self, broker: UpstoxBroker, sdk: FakeUpstoxClient) -> None:
        order = await broker.submit_order_async(asset="NSE:RELIANCE", quantity=10)
        assert await broker.cancel_order_async(order.order_id) is True


class TestPending:
    async def test_only_open_returned(self, broker: UpstoxBroker, sdk: FakeUpstoxClient) -> None:
        sdk._orders = [
            {
                "order_id": "X1",
                "status": "open",
                "exchange": "NSE",
                "tradingsymbol": "A",
                "transaction_type": "BUY",
                "quantity": 5,
                "filled_quantity": 0,
            },
            {
                "order_id": "X2",
                "status": "complete",
                "exchange": "NSE",
                "tradingsymbol": "B",
                "transaction_type": "SELL",
                "quantity": 5,
                "filled_quantity": 5,
            },
        ]
        pending = await broker.get_pending_orders_async()
        assert [p.order_id for p in pending] == ["X1"]
        assert pending[0].status == OrderStatus.PENDING

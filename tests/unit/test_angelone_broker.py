"""Tests for :class:`ml4t.india.live.angelone_broker.AngelOneBroker`."""

from __future__ import annotations

from typing import Any

import pytest
from ml4t.backtest.types import OrderSide, OrderStatus, OrderType

from ml4t.india.core.exceptions import InvalidInputError
from ml4t.india.live.angelone_broker import AngelOneBroker
from ml4t.india.live.base import IndianBrokerBase


class FakeAngelClient:
    """Minimal fake for Angel SmartAPI surface."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, tuple[Any, ...], dict[str, Any]]] = []
        self._profile: dict[str, Any] = {"data": {"clientcode": "A123"}}
        self._rms: dict[str, Any] = {"data": {"availablecash": 50000.0}}
        self._positions: dict[str, Any] = {"data": []}
        self._orders: dict[str, Any] = {"data": []}
        self._next: int = 1

    def _record(self, method: str, *args: Any, **kwargs: Any) -> None:
        self.calls.append((method, args, kwargs))

    def getProfile(self, refresh_token: str | None = None) -> dict[str, Any]:
        self._record("getProfile", refresh_token=refresh_token)
        return self._profile

    def rmsLimit(self) -> dict[str, Any]:
        self._record("rmsLimit")
        return self._rms

    def position(self) -> dict[str, Any]:
        self._record("position")
        return self._positions

    def placeOrder(self, orderparams: dict[str, Any]) -> str:
        self._record("placeOrder", orderparams)
        order_id = f"ANG-{self._next:06d}"
        self._next += 1
        self._orders["data"].append(
            {
                "orderid": order_id,
                "orderstatus": "open",
                "exchange": orderparams["exchange"],
                "tradingsymbol": orderparams["tradingsymbol"],
                "transactiontype": orderparams["transactiontype"],
                "quantity": orderparams["quantity"],
                "filledshares": 0,
            }
        )
        return order_id

    def cancelOrder(self, order_id: str, variety: str = "NORMAL") -> dict[str, Any]:
        self._record("cancelOrder", order_id, variety)
        for o in self._orders["data"]:
            if o["orderid"] == order_id:
                o["orderstatus"] = "cancelled"
        return {"status": True, "data": {"status": True}}

    def orderBook(self) -> dict[str, Any]:
        self._record("orderBook")
        return {"data": [dict(o) for o in self._orders["data"]]}


@pytest.fixture
def sdk() -> FakeAngelClient:
    return FakeAngelClient()


@pytest.fixture
def broker(sdk: FakeAngelClient) -> AngelOneBroker:
    return AngelOneBroker(sdk)


class TestInheritance:
    def test_is_indian_broker_base(self, broker: AngelOneBroker) -> None:
        assert isinstance(broker, IndianBrokerBase)


class TestConnection:
    async def test_connect_probes_profile(
        self, broker: AngelOneBroker, sdk: FakeAngelClient
    ) -> None:
        await broker.connect()
        assert any(c[0] == "getProfile" for c in sdk.calls)

    async def test_idempotent(self, broker: AngelOneBroker, sdk: FakeAngelClient) -> None:
        await broker.connect()
        await broker.connect()
        assert len([c for c in sdk.calls if c[0] == "getProfile"]) == 1


class TestAccount:
    async def test_cash_from_rms(self, broker: AngelOneBroker) -> None:
        assert await broker.get_cash_async() == pytest.approx(50000.0)


class TestOrders:
    async def test_market_buy(self, broker: AngelOneBroker, sdk: FakeAngelClient) -> None:
        order = await broker.submit_order_async(
            asset="NSE:RELIANCE", quantity=10, symboltoken="2885"
        )
        assert order.side == OrderSide.BUY
        orderparams = next(c for c in sdk.calls if c[0] == "placeOrder")[1][0]
        assert orderparams["tradingsymbol"] == "RELIANCE"
        assert orderparams["transactiontype"] == "BUY"
        assert orderparams["ordertype"] == "MARKET"
        assert orderparams["producttype"] == "DELIVERY"

    async def test_stop_order(self, broker: AngelOneBroker, sdk: FakeAngelClient) -> None:
        await broker.submit_order_async(
            asset="NSE:RELIANCE",
            quantity=10,
            order_type=OrderType.STOP,
            stop_price=2450.0,
            symboltoken="2885",
        )
        orderparams = next(c for c in sdk.calls if c[0] == "placeOrder")[1][0]
        assert orderparams["ordertype"] == "STOPLOSS_MARKET"
        assert orderparams["triggerprice"] == "2450.0"

    async def test_trailing_stop_rejected(self, broker: AngelOneBroker) -> None:
        with pytest.raises(InvalidInputError, match="order_type"):
            await broker.submit_order_async(
                asset="NSE:RELIANCE",
                quantity=10,
                order_type=OrderType.TRAILING_STOP,
            )

    async def test_bare_symbol_rejected(self, broker: AngelOneBroker) -> None:
        with pytest.raises(InvalidInputError, match="EXCHANGE:SYMBOL"):
            await broker.submit_order_async(asset="RELIANCE", quantity=10)

    async def test_cancel_echoes_true(self, broker: AngelOneBroker, sdk: FakeAngelClient) -> None:
        order = await broker.submit_order_async(
            asset="NSE:RELIANCE", quantity=10, symboltoken="2885"
        )
        assert await broker.cancel_order_async(order.order_id) is True


class TestPending:
    async def test_only_open_returned(self, broker: AngelOneBroker, sdk: FakeAngelClient) -> None:
        sdk._orders["data"] = [
            {
                "orderid": "A1",
                "orderstatus": "open",
                "exchange": "NSE",
                "tradingsymbol": "X",
                "transactiontype": "BUY",
                "quantity": 5,
                "filledshares": 0,
            },
            {
                "orderid": "A2",
                "orderstatus": "complete",
                "exchange": "NSE",
                "tradingsymbol": "Y",
                "transactiontype": "SELL",
                "quantity": 5,
                "filledshares": 5,
            },
        ]
        pending = await broker.get_pending_orders_async()
        assert [p.order_id for p in pending] == ["A1"]
        assert pending[0].status == OrderStatus.PENDING

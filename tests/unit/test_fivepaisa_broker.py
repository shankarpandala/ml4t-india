"""Tests for :class:`ml4t.india.live.fivepaisa_broker.FivePaisaBroker`."""

from __future__ import annotations

from typing import Any

import pytest
from ml4t.backtest.types import OrderSide, OrderStatus, OrderType

from ml4t.india.core.exceptions import InvalidInputError
from ml4t.india.live.base import IndianBrokerBase
from ml4t.india.live.fivepaisa_broker import FivePaisaBroker


class FakeFivePaisaClient:
    """Minimal fake for py5paisa FivePaisaClient."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, tuple[Any, ...], dict[str, Any]]] = []
        self._margin: list[dict[str, Any]] = [{"Segment": "Equity", "AvailableMargin": 75000.0}]
        self._positions: list[dict[str, Any]] = []
        self._orders: list[dict[str, Any]] = []
        self._next: int = 1

    def _record(self, method: str, *args: Any, **kwargs: Any) -> None:
        self.calls.append((method, args, kwargs))

    def get_client_info(self) -> dict[str, Any]:
        self._record("get_client_info")
        return {"ClientCode": "ABC123"}

    def margin(self) -> list[dict[str, Any]]:
        self._record("margin")
        return list(self._margin)

    def positions(self) -> list[dict[str, Any]]:
        self._record("positions")
        return list(self._positions)

    def place_order(self, **kwargs: Any) -> dict[str, Any]:
        self._record("place_order", **kwargs)
        order_id = f"5P-{self._next:06d}"
        self._next += 1
        self._orders.append(
            {
                "ExchOrderID": order_id,
                "OrderStatus": "Pending",
                "Exch": kwargs.get("Exchange", "N"),
                "ScripName": "FAKE",
                "BuySell": kwargs.get("OrderType", "B"),
                "Qty": kwargs.get("Qty", 0),
                "TradedQty": 0,
            }
        )
        return {"ExchOrderID": order_id, "Status": 0}

    def cancel_order(self, exch_order_id: str) -> dict[str, Any]:
        self._record("cancel_order", exch_order_id)
        for o in self._orders:
            if o["ExchOrderID"] == exch_order_id:
                o["OrderStatus"] = "Cancelled"
        return {"Status": 0}

    def order_book(self) -> list[dict[str, Any]]:
        self._record("order_book")
        return [dict(o) for o in self._orders]


@pytest.fixture
def sdk() -> FakeFivePaisaClient:
    return FakeFivePaisaClient()


@pytest.fixture
def broker(sdk: FakeFivePaisaClient) -> FivePaisaBroker:
    return FivePaisaBroker(sdk)


class TestInheritance:
    def test_is_indian_broker_base(self, broker: FivePaisaBroker) -> None:
        assert isinstance(broker, IndianBrokerBase)


class TestConnection:
    async def test_connect_probes(self, broker: FivePaisaBroker, sdk: FakeFivePaisaClient) -> None:
        await broker.connect()
        assert any(c[0] == "get_client_info" for c in sdk.calls)


class TestAccount:
    async def test_cash_from_margin(self, broker: FivePaisaBroker) -> None:
        assert await broker.get_cash_async() == pytest.approx(75000.0)

    async def test_cash_defaults_zero_on_missing(
        self, broker: FivePaisaBroker, sdk: FakeFivePaisaClient
    ) -> None:
        sdk._margin = []
        assert await broker.get_cash_async() == 0.0


class TestOrders:
    async def test_market_buy(self, broker: FivePaisaBroker, sdk: FakeFivePaisaClient) -> None:
        order = await broker.submit_order_async(asset="NSE:RELIANCE", quantity=10, scrip_code=2885)
        assert order.side == OrderSide.BUY
        call = next(c for c in sdk.calls if c[0] == "place_order")[2]
        assert call["OrderType"] == "B"
        assert call["Exchange"] == "NSE"
        assert call["Qty"] == 10

    async def test_limit_sell_negative_qty(
        self, broker: FivePaisaBroker, sdk: FakeFivePaisaClient
    ) -> None:
        order = await broker.submit_order_async(
            asset="NSE:TCS",
            quantity=-5,
            order_type=OrderType.LIMIT,
            limit_price=3800.0,
            scrip_code=11536,
        )
        assert order.side == OrderSide.SELL
        call = next(c for c in sdk.calls if c[0] == "place_order")[2]
        assert call["OrderType"] == "S"
        assert call["Price"] == 3800.0

    async def test_stop_flips_is_stop_loss(
        self, broker: FivePaisaBroker, sdk: FakeFivePaisaClient
    ) -> None:
        await broker.submit_order_async(
            asset="NSE:RELIANCE",
            quantity=10,
            order_type=OrderType.STOP,
            stop_price=2450.0,
            scrip_code=2885,
        )
        call = next(c for c in sdk.calls if c[0] == "place_order")[2]
        assert call["IsStopLossOrder"] is True
        assert call["StopLossPrice"] == 2450.0

    async def test_trailing_stop_rejected(self, broker: FivePaisaBroker) -> None:
        with pytest.raises(InvalidInputError, match="order_type"):
            await broker.submit_order_async(
                asset="NSE:RELIANCE",
                quantity=10,
                order_type=OrderType.TRAILING_STOP,
            )

    async def test_bare_symbol_rejected(self, broker: FivePaisaBroker) -> None:
        with pytest.raises(InvalidInputError, match="EXCHANGE:SYMBOL"):
            await broker.submit_order_async(asset="RELIANCE", quantity=10)

    async def test_cancel_echoes_true(
        self, broker: FivePaisaBroker, sdk: FakeFivePaisaClient
    ) -> None:
        order = await broker.submit_order_async(asset="NSE:RELIANCE", quantity=10, scrip_code=2885)
        assert await broker.cancel_order_async(order.order_id) is True


class TestReplace:
    async def test_replace_quantity(
        self, broker: FivePaisaBroker, sdk: FakeFivePaisaClient
    ) -> None:
        sdk.modify_order = lambda exch_order_id, **kw: {  # type: ignore[attr-defined]  # noqa: ARG005
            "ExchOrderID": exch_order_id,
            "Status": 0,
        }
        original = await broker.submit_order_async(
            asset="NSE:RELIANCE", quantity=10, scrip_code=2885
        )
        replaced = await broker.replace_order_async(original.order_id, quantity=20)
        assert replaced.order_id == original.order_id

    async def test_replace_no_args_rejected(self, broker: FivePaisaBroker) -> None:
        with pytest.raises(InvalidInputError, match="at least one"):
            await broker.replace_order_async("FAKE")


class TestMOC:
    async def test_moc_market_translation(
        self, broker: FivePaisaBroker, sdk: FakeFivePaisaClient
    ) -> None:
        moc = getattr(OrderType, "MOC", None)
        if moc is None:
            pytest.skip("upstream OrderType has no MOC yet")
        await broker.submit_order_async(
            asset="NSE:RELIANCE",
            quantity=10,
            order_type=moc,
            scrip_code=2885,
        )
        place = [c for c in sdk.calls if c[0] == "place_order"]
        # MOC routes through the same place_order; IsStopLossOrder must not
        # be flipped on (that's for STOP / STOP_LIMIT).
        assert place[0][2]["IsStopLossOrder"] is False


class TestPending:
    async def test_only_pending_returned(
        self, broker: FivePaisaBroker, sdk: FakeFivePaisaClient
    ) -> None:
        sdk._orders = [
            {
                "ExchOrderID": "X1",
                "OrderStatus": "Pending",
                "Exch": "NSE",
                "ScripName": "A",
                "BuySell": "B",
                "Qty": 5,
                "TradedQty": 0,
            },
            {
                "ExchOrderID": "X2",
                "OrderStatus": "Fully Executed",
                "Exch": "NSE",
                "ScripName": "B",
                "BuySell": "S",
                "Qty": 5,
                "TradedQty": 5,
            },
        ]
        pending = await broker.get_pending_orders_async()
        assert [p.order_id for p in pending] == ["X1"]
        assert pending[0].status == OrderStatus.PENDING

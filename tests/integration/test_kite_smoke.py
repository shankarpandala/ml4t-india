"""Full real-broker smoke test for KiteBroker against live Kite Connect.

Run with:
    pytest -m integration -v

Requires credentials stored via:
    python scripts/store_kite_credentials.py

The order round-trip uses a Rs 1 LIMIT BUY for 1 share of INFY — far off-market,
zero fill risk. All assertions are structural (types and presence), never price-based.
"""

from __future__ import annotations

import pytest
from ml4t.backtest.types import OrderSide, OrderStatus, OrderType

from ml4t.india.live.kite_broker import KiteBroker


@pytest.mark.integration
class TestKiteLiveBroker:
    async def test_is_connected(self, kite_broker: KiteBroker) -> None:
        assert await kite_broker.is_connected_async() is True

    async def test_get_cash(self, kite_broker: KiteBroker) -> None:
        cash = await kite_broker.get_cash_async()
        assert isinstance(cash, float)
        assert cash >= 0.0

    async def test_get_account_value(self, kite_broker: KiteBroker) -> None:
        value = await kite_broker.get_account_value_async()
        assert isinstance(value, float)
        assert value >= 0.0

    async def test_get_positions(self, kite_broker: KiteBroker) -> None:
        positions = await kite_broker.get_positions_async()
        assert isinstance(positions, dict)
        for asset, pos in positions.items():
            assert ":" in asset, f"asset missing EXCHANGE: prefix: {asset!r}"
            assert pos.asset == asset
            assert pos.quantity != 0

    async def test_order_roundtrip(self, kite_broker: KiteBroker) -> None:
        # Place far-off-market limit order — Rs 1 guarantees zero fill
        order = await kite_broker.submit_order_async(
            asset="NSE:INFY",
            quantity=1,
            side=OrderSide.BUY,
            order_type=OrderType.LIMIT,
            limit_price=1.0,
            product="CNC",
        )
        assert order.order_id, "Expected a non-empty order_id from Kite"
        assert order.status == OrderStatus.PENDING
        assert order.asset == "NSE:INFY"
        assert order.side == OrderSide.BUY
        assert order.quantity == 1.0
        assert order.limit_price == 1.0

        # Confirm the order appears in the pending list
        pending = await kite_broker.get_pending_orders_async()
        pending_ids = [o.order_id for o in pending]
        assert order.order_id in pending_ids, (
            f"Order {order.order_id!r} not found in pending orders: {pending_ids}"
        )

        # Cancel and verify acceptance
        cancelled = await kite_broker.cancel_order_async(order.order_id)
        assert cancelled is True

    async def test_disconnect(self, kite_broker: KiteBroker) -> None:
        await kite_broker.disconnect()
        assert await kite_broker.is_connected_async() is False

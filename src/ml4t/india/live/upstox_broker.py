"""Upstox broker implementation of :class:`IndianBrokerBase`.

Upstox's Python SDK (``upstox-python-sdk``) is REST-first with a
different call surface than kiteconnect. Rather than subclass
:class:`AsyncKiteClient`, this broker takes a generic duck-typed client
and calls upstox-shaped methods on it:

* ``get_profile()`` (connect probe)
* ``get_funds_and_margin(segment=...)``
* ``get_positions()``
* ``place_order(...)`` returns ``order_id``
* ``cancel_order(order_id, ...)``
* ``get_order_book()``

An integrator installs ``upstox-python-sdk`` and constructs the SDK
client; tests in :mod:`tests.unit.test_upstox_broker` inject a fake
that records calls -- same pattern as :class:`KiteBroker` with
:class:`FakeKiteClient`.

This phase only ships the translation layer; the Upstox SDK itself
is NOT a pinned dependency. If/when the upstream ml4t-live ecosystem
needs it, add ``ml4t-india[upstox]`` extras.
"""

from __future__ import annotations

import asyncio
import datetime as dt
from typing import Any, Protocol, runtime_checkable

from ml4t.backtest.types import Order, OrderSide, OrderStatus, OrderType, Position

from ml4t.india.core.exceptions import InvalidInputError
from ml4t.india.live.base import IndianBrokerBase


@runtime_checkable
class UpstoxClientProtocol(Protocol):
    """Structural shape we depend on from ``upstox-python-sdk``."""

    def get_profile(self) -> dict[str, Any]: ...
    def get_funds_and_margin(self, segment: str = ...) -> dict[str, Any]: ...
    def get_positions(self) -> list[dict[str, Any]]: ...
    def place_order(self, **kwargs: Any) -> str: ...
    def cancel_order(self, order_id: str, **kwargs: Any) -> str: ...
    def get_order_book(self) -> list[dict[str, Any]]: ...


_UPSTOX_STATUS: dict[str, OrderStatus] = {
    "complete": OrderStatus.FILLED,
    "cancelled": OrderStatus.CANCELLED,
    "rejected": OrderStatus.REJECTED,
    "open": OrderStatus.PENDING,
    "pending": OrderStatus.PENDING,
    "trigger pending": OrderStatus.PENDING,
    "after market order req received": OrderStatus.PENDING,
}


def _upstox_order_type(ot: OrderType) -> str:
    """Upstream OrderType -> Upstox wire value."""
    mapping = {
        OrderType.MARKET: "MARKET",
        OrderType.LIMIT: "LIMIT",
        OrderType.STOP: "SL-M",
        OrderType.STOP_LIMIT: "SL",
    }
    if ot not in mapping:
        raise InvalidInputError(
            f"Upstox does not support order_type={ot.value!r}. "
            "TRAILING_STOP must be simulated strategy-side."
        )
    return mapping[ot]


def _split_asset(asset: str) -> tuple[str, str]:
    if ":" not in asset:
        raise InvalidInputError(f"asset must be 'EXCHANGE:SYMBOL' (got {asset!r})")
    exchange, tradingsymbol = asset.split(":", 1)
    return exchange.upper(), tradingsymbol


class UpstoxBroker(IndianBrokerBase):
    """Upstox implementation of the Indian broker base class.

    Parameters
    ----------
    client:
        Anything satisfying :class:`UpstoxClientProtocol`. In production
        this is an ``upstox_client.ApiClient`` + auth. In tests, a
        recorder fake.
    """

    def __init__(self, client: UpstoxClientProtocol) -> None:
        self._client = client
        self._connected: bool = False

    # ---- connection lifecycle --------------------------------------

    async def connect(self) -> None:
        if self._connected:
            return
        await asyncio.to_thread(self._client.get_profile)
        self._connected = True

    async def disconnect(self) -> None:
        self._connected = False

    async def is_connected_async(self) -> bool:
        return self._connected

    # ---- account ---------------------------------------------------

    async def get_cash_async(self) -> float:
        data = await asyncio.to_thread(self._client.get_funds_and_margin, "equity")
        equity = data.get("equity", data) if isinstance(data, dict) else {}
        return float(equity.get("available_margin", 0.0))

    async def get_account_value_async(self) -> float:
        cash = await self.get_cash_async()
        positions = await asyncio.to_thread(self._client.get_positions)
        mtm = 0.0
        for row in positions or []:
            qty = float(row.get("quantity", 0))
            if qty == 0:
                continue
            px = float(row.get("last_price") or row.get("average_price") or 0.0)
            mult = float(row.get("multiplier", 1) or 1)
            mtm += qty * mult * px
        return cash + mtm

    # ---- positions -------------------------------------------------

    async def get_positions_async(self) -> dict[str, Position]:
        rows = await asyncio.to_thread(self._client.get_positions) or []
        out: dict[str, Position] = {}
        for row in rows:
            qty = float(row.get("quantity", 0))
            if qty == 0:
                continue
            asset = f"{row.get('exchange', 'NSE')}:{row.get('tradingsymbol', '')}"
            out[asset] = Position(
                asset=asset,
                quantity=qty,
                entry_price=float(row.get("average_price", 0.0)),
                entry_time=dt.datetime.now(dt.UTC),
                current_price=float(row.get("last_price") or 0.0) or None,
                multiplier=float(row.get("multiplier", 1) or 1),
            )
        return out

    async def get_position_async(self, asset: str) -> Position | None:
        positions = await self.get_positions_async()
        return positions.get(asset)

    async def close_position_async(self, asset: str) -> Order | None:
        position = await self.get_position_async(asset)
        if position is None or position.quantity == 0:
            return None
        side = OrderSide.SELL if position.quantity > 0 else OrderSide.BUY
        return await self.submit_order_async(
            asset=asset,
            quantity=abs(position.quantity),
            side=side,
            order_type=OrderType.MARKET,
        )

    # ---- orders ----------------------------------------------------

    async def submit_order_async(
        self,
        asset: str,
        quantity: float,
        side: OrderSide | None = None,
        order_type: OrderType = OrderType.MARKET,
        limit_price: float | None = None,
        stop_price: float | None = None,
        **kwargs: Any,
    ) -> Order:
        exchange, tradingsymbol = _split_asset(asset)
        if side is None:
            side = OrderSide.BUY if quantity >= 0 else OrderSide.SELL
        abs_qty = int(abs(quantity))
        if abs_qty == 0:
            raise InvalidInputError("quantity must be nonzero")

        payload: dict[str, Any] = {
            "exchange": exchange,
            "tradingsymbol": tradingsymbol,
            "transaction_type": "BUY" if side == OrderSide.BUY else "SELL",
            "quantity": abs_qty,
            "order_type": _upstox_order_type(order_type),
            "product": kwargs.pop("product", "D"),  # D=delivery, I=intraday, MTF
            **kwargs,
        }
        if limit_price is not None:
            payload["price"] = limit_price
        if stop_price is not None:
            payload["trigger_price"] = stop_price

        order_id = await asyncio.to_thread(lambda: self._client.place_order(**payload))
        return Order(
            asset=asset,
            side=side,
            quantity=float(abs_qty),
            order_type=order_type,
            limit_price=limit_price,
            stop_price=stop_price,
            order_id=str(order_id),
            status=OrderStatus.PENDING,
        )

    async def cancel_order_async(self, order_id: str) -> bool:
        echoed = await asyncio.to_thread(self._client.cancel_order, order_id)
        return str(echoed) == str(order_id)

    async def get_pending_orders_async(self) -> list[Order]:
        rows = await asyncio.to_thread(self._client.get_order_book) or []
        orders: list[Order] = []
        for row in rows:
            status = _UPSTOX_STATUS.get(str(row.get("status", "")).lower(), OrderStatus.PENDING)
            if status != OrderStatus.PENDING:
                continue
            asset = f"{row.get('exchange', 'NSE')}:{row.get('tradingsymbol', '')}"
            side = (
                OrderSide.BUY
                if str(row.get("transaction_type", "BUY")).upper() == "BUY"
                else OrderSide.SELL
            )
            orders.append(
                Order(
                    asset=asset,
                    side=side,
                    quantity=float(row.get("quantity", 0)),
                    order_type=OrderType.MARKET,
                    order_id=str(row.get("order_id", "")),
                    status=status,
                    filled_quantity=float(row.get("filled_quantity", 0) or 0),
                )
            )
        return orders


__all__ = ["UpstoxBroker", "UpstoxClientProtocol"]

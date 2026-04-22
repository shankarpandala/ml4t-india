"""Angel One (SmartAPI) broker implementation of :class:`IndianBrokerBase`.

Angel's ``SmartApi-python`` SDK is REST + WebSocket. Order placement
uses a single ``place_order(orderparams)`` call with a dict payload;
token-based auth is managed by the SDK. Structural protocol below
documents the subset of ``SmartConnect`` we depend on.

Like :class:`UpstoxBroker`, this class is injectable with a fake
client for unit tests. Production code plugs in a real
``SmartConnect`` session.
"""

from __future__ import annotations

import asyncio
import datetime as dt
from typing import Any, Protocol, runtime_checkable

from ml4t.backtest.types import Order, OrderSide, OrderStatus, OrderType, Position

from ml4t.india.core.exceptions import InvalidInputError
from ml4t.india.live.base import IndianBrokerBase


@runtime_checkable
class AngelClientProtocol(Protocol):
    """Structural shape we depend on from ``SmartApi``."""

    def getProfile(self, refresh_token: str | None = ...) -> dict[str, Any]: ...
    def rmsLimit(self) -> dict[str, Any]: ...
    def position(self) -> dict[str, Any]: ...
    def placeOrder(self, orderparams: dict[str, Any]) -> str: ...
    def cancelOrder(self, order_id: str, variety: str = ...) -> dict[str, Any]: ...
    def orderBook(self) -> dict[str, Any]: ...


_ANGEL_STATUS: dict[str, OrderStatus] = {
    "complete": OrderStatus.FILLED,
    "cancelled": OrderStatus.CANCELLED,
    "rejected": OrderStatus.REJECTED,
    "open": OrderStatus.PENDING,
    "trigger pending": OrderStatus.PENDING,
    "validation pending": OrderStatus.PENDING,
    "open pending": OrderStatus.PENDING,
}


def _angel_order_type(ot: OrderType) -> str:
    mapping = {
        OrderType.MARKET: "MARKET",
        OrderType.LIMIT: "LIMIT",
        OrderType.STOP: "STOPLOSS_MARKET",
        OrderType.STOP_LIMIT: "STOPLOSS_LIMIT",
    }
    if ot not in mapping:
        raise InvalidInputError(
            f"Angel One does not support order_type={ot.value!r}. "
            "TRAILING_STOP must be simulated strategy-side."
        )
    return mapping[ot]


def _split_asset(asset: str) -> tuple[str, str]:
    if ":" not in asset:
        raise InvalidInputError(f"asset must be 'EXCHANGE:SYMBOL' (got {asset!r})")
    exchange, tradingsymbol = asset.split(":", 1)
    return exchange.upper(), tradingsymbol


class AngelOneBroker(IndianBrokerBase):
    """Angel One SmartAPI broker.

    Parameters
    ----------
    client:
        Anything satisfying :class:`AngelClientProtocol`. Production
        wires a real ``SmartConnect`` instance that has gone through
        the MPIN + TOTP + auth flow.
    """

    def __init__(self, client: AngelClientProtocol) -> None:
        self._client = client
        self._connected: bool = False

    # ---- connection lifecycle --------------------------------------

    async def connect(self) -> None:
        if self._connected:
            return
        await asyncio.to_thread(self._client.getProfile)
        self._connected = True

    async def disconnect(self) -> None:
        self._connected = False

    async def is_connected_async(self) -> bool:
        return self._connected

    # ---- account ---------------------------------------------------

    async def get_cash_async(self) -> float:
        data = await asyncio.to_thread(self._client.rmsLimit)
        d = data.get("data", data) if isinstance(data, dict) else {}
        return float(d.get("availablecash", 0.0) or 0.0)

    async def get_account_value_async(self) -> float:
        cash = await self.get_cash_async()
        positions = await asyncio.to_thread(self._client.position)
        rows = positions.get("data", []) if isinstance(positions, dict) else []
        mtm = 0.0
        for row in rows or []:
            qty = float(row.get("netqty", row.get("quantity", 0)) or 0)
            if qty == 0:
                continue
            px = float(row.get("ltp", row.get("buyavgprice", 0)) or 0)
            mult = float(row.get("multiplier", 1) or 1)
            mtm += qty * mult * px
        return cash + mtm

    # ---- positions -------------------------------------------------

    async def get_positions_async(self) -> dict[str, Position]:
        data = await asyncio.to_thread(self._client.position)
        rows = data.get("data", []) if isinstance(data, dict) else []
        out: dict[str, Position] = {}
        for row in rows or []:
            qty = float(row.get("netqty", row.get("quantity", 0)) or 0)
            if qty == 0:
                continue
            asset = f"{row.get('exchange', 'NSE')}:{row.get('tradingsymbol', '')}"
            out[asset] = Position(
                asset=asset,
                quantity=qty,
                entry_price=float(row.get("buyavgprice", 0.0) or 0),
                entry_time=dt.datetime.now(dt.UTC),
                current_price=float(row.get("ltp") or 0.0) or None,
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

        orderparams: dict[str, Any] = {
            "variety": kwargs.pop("variety", "NORMAL"),
            "tradingsymbol": tradingsymbol,
            "exchange": exchange,
            "transactiontype": "BUY" if side == OrderSide.BUY else "SELL",
            "ordertype": _angel_order_type(order_type),
            "producttype": kwargs.pop("product", "DELIVERY"),
            "duration": kwargs.pop("duration", "DAY"),
            "quantity": abs_qty,
            # Angel requires symboltoken; caller must supply via kwargs or
            # the strategy resolves it before calling us.
            **kwargs,
        }
        if limit_price is not None:
            orderparams["price"] = str(limit_price)
        if stop_price is not None:
            orderparams["triggerprice"] = str(stop_price)

        order_id = await asyncio.to_thread(self._client.placeOrder, orderparams)
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

    async def cancel_order_async(self, order_id: str, variety: str = "NORMAL") -> bool:
        result = await asyncio.to_thread(self._client.cancelOrder, order_id, variety)
        if isinstance(result, dict):
            # SmartAPI wraps status in data.status
            data = result.get("data", result)
            return bool(data.get("status", result.get("status", False)))
        return bool(result)

    async def get_pending_orders_async(self) -> list[Order]:
        data = await asyncio.to_thread(self._client.orderBook)
        rows = data.get("data", []) if isinstance(data, dict) else []
        orders: list[Order] = []
        for row in rows or []:
            status = _ANGEL_STATUS.get(str(row.get("orderstatus", "")).lower(), OrderStatus.PENDING)
            if status != OrderStatus.PENDING:
                continue
            asset = f"{row.get('exchange', 'NSE')}:{row.get('tradingsymbol', '')}"
            side = (
                OrderSide.BUY
                if str(row.get("transactiontype", "BUY")).upper() == "BUY"
                else OrderSide.SELL
            )
            orders.append(
                Order(
                    asset=asset,
                    side=side,
                    quantity=float(row.get("quantity", 0) or 0),
                    order_type=OrderType.MARKET,
                    order_id=str(row.get("orderid", "")),
                    status=status,
                    filled_quantity=float(row.get("filledshares", 0) or 0),
                )
            )
        return orders


__all__ = ["AngelOneBroker", "AngelClientProtocol"]

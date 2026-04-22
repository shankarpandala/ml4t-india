"""5paisa broker implementation of :class:`IndianBrokerBase`.

5paisa's ``py5paisa`` SDK uses a ``FivePaisaClient`` with methods like
``place_order``, ``cancel_order``, ``positions``, ``order_book``,
``margin``. Auth flow is TOTP + session token.

As with the other broker adapters, this class takes any object
implementing :class:`FivePaisaClientProtocol`; production supplies the
real SDK client, tests use a fake.
"""

from __future__ import annotations

import asyncio
import datetime as dt
from typing import Any, Protocol, runtime_checkable

from ml4t.backtest.types import Order, OrderSide, OrderStatus, OrderType, Position

from ml4t.india.core.exceptions import InvalidInputError
from ml4t.india.live.base import IndianBrokerBase


@runtime_checkable
class FivePaisaClientProtocol(Protocol):
    """Structural shape we depend on from ``py5paisa.FivePaisaClient``."""

    def get_client_info(self) -> dict[str, Any]: ...
    def margin(self) -> list[dict[str, Any]]: ...
    def positions(self) -> list[dict[str, Any]]: ...
    def place_order(self, **kwargs: Any) -> dict[str, Any]: ...
    def cancel_order(self, exch_order_id: str) -> dict[str, Any]: ...
    def order_book(self) -> list[dict[str, Any]]: ...


# 5paisa surfaces numeric status codes (see their API docs); we map
# the full-complete/cancelled/rejected trio plus any PENDING-like state.
_FIVEPAISA_STATUS_CODES: dict[str, OrderStatus] = {
    "fully executed": OrderStatus.FILLED,
    "cancelled": OrderStatus.CANCELLED,
    "rejected": OrderStatus.REJECTED,
    "pending": OrderStatus.PENDING,
    "partial": OrderStatus.PENDING,
    "partially executed": OrderStatus.PENDING,
}


def _fivepaisa_order_type(ot: OrderType) -> tuple[bool, bool]:
    """Translate to (IsStopLossOrder, IsIOCOrder) pair 5paisa expects.

    Market + Limit use the same method with different price params;
    stops flip IsStopLossOrder.
    """
    if ot == OrderType.MARKET:
        return False, False
    if ot == OrderType.LIMIT:
        return False, False
    if ot in (OrderType.STOP, OrderType.STOP_LIMIT):
        return True, False
    raise InvalidInputError(
        f"5paisa does not support order_type={ot.value!r}. "
        "TRAILING_STOP must be simulated strategy-side."
    )


def _split_asset(asset: str) -> tuple[str, str]:
    if ":" not in asset:
        raise InvalidInputError(f"asset must be 'EXCHANGE:SYMBOL' (got {asset!r})")
    exchange, tradingsymbol = asset.split(":", 1)
    return exchange.upper(), tradingsymbol


class FivePaisaBroker(IndianBrokerBase):
    """5paisa implementation of the Indian broker base class.

    Parameters
    ----------
    client:
        Anything satisfying :class:`FivePaisaClientProtocol`. Production
        wires a logged-in ``FivePaisaClient`` instance.
    """

    def __init__(self, client: FivePaisaClientProtocol) -> None:
        self._client = client
        self._connected: bool = False

    # ---- connection lifecycle --------------------------------------

    async def connect(self) -> None:
        if self._connected:
            return
        await asyncio.to_thread(self._client.get_client_info)
        self._connected = True

    async def disconnect(self) -> None:
        self._connected = False

    async def is_connected_async(self) -> bool:
        return self._connected

    # ---- account ---------------------------------------------------

    async def get_cash_async(self) -> float:
        rows = await asyncio.to_thread(self._client.margin) or []
        # 5paisa's margin() returns a list; the first element carries
        # the AvailableMargin field on the equity row.
        for row in rows:
            if row.get("Segment") in ("Equity", "EQ", None):
                return float(row.get("AvailableMargin", 0.0) or 0.0)
        return 0.0

    async def get_account_value_async(self) -> float:
        cash = await self.get_cash_async()
        rows = await asyncio.to_thread(self._client.positions) or []
        mtm = 0.0
        for row in rows:
            qty = float(row.get("NetQty", 0) or 0)
            if qty == 0:
                continue
            px = float(row.get("LTP", row.get("BuyAvgRate", 0)) or 0)
            mult = float(row.get("Multiplier", 1) or 1)
            mtm += qty * mult * px
        return cash + mtm

    # ---- positions -------------------------------------------------

    async def get_positions_async(self) -> dict[str, Position]:
        rows = await asyncio.to_thread(self._client.positions) or []
        out: dict[str, Position] = {}
        for row in rows:
            qty = float(row.get("NetQty", 0) or 0)
            if qty == 0:
                continue
            asset = f"{row.get('Exch', 'NSE')}:{row.get('ScripName', '')}"
            out[asset] = Position(
                asset=asset,
                quantity=qty,
                entry_price=float(row.get("BuyAvgRate", 0.0) or 0),
                entry_time=dt.datetime.now(dt.UTC),
                current_price=float(row.get("LTP") or 0.0) or None,
                multiplier=float(row.get("Multiplier", 1) or 1),
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

        is_stop, is_ioc = _fivepaisa_order_type(order_type)

        payload: dict[str, Any] = {
            "OrderType": "B" if side == OrderSide.BUY else "S",
            "Exchange": exchange,
            "ExchangeType": kwargs.pop("exchange_type", "C"),  # C=cash, D=derivative
            "ScripCode": kwargs.pop("scrip_code", 0),  # caller resolves via InstrumentsCache
            "Qty": abs_qty,
            "Price": limit_price or 0,
            "IsIntraday": kwargs.pop("is_intraday", False),
            "IsStopLossOrder": is_stop,
            "StopLossPrice": stop_price or 0,
            "IsIOCOrder": is_ioc,
            "RemoteOrderID": kwargs.pop("remote_order_id", ""),
            **kwargs,
        }
        # unused in our dispatch but kept for API parity
        _ = tradingsymbol

        result = await asyncio.to_thread(lambda: self._client.place_order(**payload))
        order_id = str(result.get("ExchOrderID", "")) if isinstance(result, dict) else str(result)
        return Order(
            asset=asset,
            side=side,
            quantity=float(abs_qty),
            order_type=order_type,
            limit_price=limit_price,
            stop_price=stop_price,
            order_id=order_id,
            status=OrderStatus.PENDING,
        )

    async def cancel_order_async(self, order_id: str) -> bool:
        result = await asyncio.to_thread(self._client.cancel_order, order_id)
        if isinstance(result, dict):
            return result.get("Status", 1) == 0  # 0 == success in 5paisa
        return bool(result)

    async def get_pending_orders_async(self) -> list[Order]:
        rows = await asyncio.to_thread(self._client.order_book) or []
        orders: list[Order] = []
        for row in rows:
            status = _FIVEPAISA_STATUS_CODES.get(
                str(row.get("OrderStatus", "")).lower(), OrderStatus.PENDING
            )
            if status != OrderStatus.PENDING:
                continue
            asset = f"{row.get('Exch', 'NSE')}:{row.get('ScripName', '')}"
            side = OrderSide.BUY if str(row.get("BuySell", "B")).upper() == "B" else OrderSide.SELL
            orders.append(
                Order(
                    asset=asset,
                    side=side,
                    quantity=float(row.get("Qty", 0) or 0),
                    order_type=OrderType.MARKET,
                    order_id=str(row.get("ExchOrderID", "")),
                    status=status,
                    filled_quantity=float(row.get("TradedQty", 0) or 0),
                )
            )
        return orders


__all__ = ["FivePaisaBroker", "FivePaisaClientProtocol"]

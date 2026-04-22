"""Concrete Zerodha Kite implementation of :class:`IndianBrokerBase`.

:class:`KiteBroker` translates the upstream broker protocol (``Order`` /
``Position`` / ``OrderSide`` / ``OrderType`` from ``ml4t.backtest.types``)
into Kite Connect's wire vocabulary (``variety`` / ``exchange`` /
``tradingsymbol`` / ``product`` / ``transaction_type``) and back again.

Design
------

* Kite's HTTP API is effectively stateless; "connection" is really
  "is the access token still valid?". :meth:`connect` probes with
  :meth:`AsyncKiteClient.profile` and caches the result; :meth:`disconnect`
  flips a local flag and is otherwise a no-op.

* Every SDK call runs through :class:`AsyncKiteClient`, which layers
  rate-limit token buckets + :func:`translate` on top of ``kiteconnect``.
  KiteBroker itself adds *no* new error translation; it just converts
  dict payloads to upstream dataclasses.

* ``asset`` is the canonical ``EXCHANGE:SYMBOL`` string used everywhere
  in ml4t-india. The broker splits it on ``:`` to feed Kite's separate
  ``exchange`` and ``tradingsymbol`` fields. Callers who pass a bare
  symbol get a :class:`ValueError` so the mistake surfaces loudly.

* Default product selection: ``CNC`` for ``NSE`` / ``BSE`` equity,
  ``NRML`` everywhere else. Callers override via ``product=`` kwarg
  to :meth:`submit_order_async`.

* Lot-size rounding is NOT applied here. Callers are expected to use
  :func:`~ml4t.india.backtest.lot_sizing.round_to_lot` or
  :func:`~ml4t.india.backtest.lot_sizing.floor_to_lot` with the lot
  size fetched from :meth:`InstrumentsCache.resolve`. Doing it here
  would silently change strategy-intended quantities.
"""

from __future__ import annotations

import datetime as dt
from typing import Any

from ml4t.backtest.types import Order, OrderSide, OrderStatus, OrderType, Position

from ml4t.india.core.constants import OrderType as KiteOrderType
from ml4t.india.core.constants import Product, TransactionType, Variety
from ml4t.india.core.exceptions import InvalidInputError
from ml4t.india.kite.client import AsyncKiteClient
from ml4t.india.live.base import IndianBrokerBase

# ---- status mapping ----------------------------------------------------
#
# Kite exposes a dozen raw status strings; upstream has a four-value
# OrderStatus enum. Many Kite states collapse to the same upstream
# value (e.g. ``VALIDATION PENDING`` and ``OPEN`` both mean PENDING).
# Unknown states fall through to ``PENDING`` -- safer than FILLED for
# risk downstream, and surfaces via the structured logger.

_KITE_STATUS: dict[str, OrderStatus] = {
    "COMPLETE": OrderStatus.FILLED,
    "CANCELLED": OrderStatus.CANCELLED,
    "REJECTED": OrderStatus.REJECTED,
    "OPEN": OrderStatus.PENDING,
    "TRIGGER PENDING": OrderStatus.PENDING,
    "VALIDATION PENDING": OrderStatus.PENDING,
    "PUT ORDER REQ RECEIVED": OrderStatus.PENDING,
    "MODIFY PENDING": OrderStatus.PENDING,
    "MODIFY VALIDATION PENDING": OrderStatus.PENDING,
    "CANCEL PENDING": OrderStatus.PENDING,
}


def _ml4t_to_kite_order_type(ot: OrderType) -> KiteOrderType:
    """Translate upstream ``OrderType`` into Kite's enum.

    Raises :class:`InvalidInputError` for order types Kite does not
    support (``TRAILING_STOP``). STOP / STOP_LIMIT both map to Kite's
    SL_M / SL respectively.
    """
    if ot == OrderType.MARKET:
        return KiteOrderType.MARKET
    if ot == OrderType.LIMIT:
        return KiteOrderType.LIMIT
    if ot == OrderType.STOP:
        return KiteOrderType.SL_M
    if ot == OrderType.STOP_LIMIT:
        return KiteOrderType.SL
    raise InvalidInputError(
        f"Kite has no native order-type equivalent for {ot.value!r}. "
        "TRAILING_STOP must be simulated strategy-side."
    )


def _split_asset(asset: str) -> tuple[str, str]:
    """Split an ``EXCHANGE:SYMBOL`` asset spec.

    Kite's ``place_order`` takes separate ``exchange`` and
    ``tradingsymbol`` fields, never a combined form.
    """
    if ":" not in asset:
        raise InvalidInputError(f"asset must be 'EXCHANGE:SYMBOL' (got {asset!r})")
    exchange, tradingsymbol = asset.split(":", 1)
    return exchange.upper(), tradingsymbol


def _default_product(exchange: str) -> Product:
    """CNC for equity cash; NRML for derivatives / currency / commodity."""
    if exchange in ("NSE", "BSE"):
        return Product.CNC
    return Product.NRML


def _to_position(row: dict[str, Any]) -> Position:
    """Convert a Kite ``positions()`` row to upstream :class:`Position`.

    Kite's dict schema:
      * ``tradingsymbol``, ``exchange``
      * ``quantity`` (signed: positive long, negative short)
      * ``average_price`` (entry avg)
      * ``last_price`` (current mark)
      * ``multiplier`` (contract multiplier; F&O options use 1)
    """
    asset = f"{row['exchange']}:{row['tradingsymbol']}"
    quantity = float(row.get("quantity", 0))
    return Position(
        asset=asset,
        quantity=quantity,
        entry_price=float(row.get("average_price", 0.0)),
        entry_time=dt.datetime.now(dt.UTC),
        current_price=float(row.get("last_price") or 0.0) or None,
        multiplier=float(row.get("multiplier", 1) or 1),
    )


def _to_order(row: dict[str, Any]) -> Order:
    """Convert a Kite ``orders()`` row to upstream :class:`Order`.

    Kite's dict schema (relevant subset):
      * ``order_id`` (broker-assigned string)
      * ``tradingsymbol``, ``exchange``
      * ``transaction_type`` (``BUY``/``SELL``)
      * ``quantity`` (absolute unsigned contract count)
      * ``filled_quantity`` (monotonically >= 0, <= quantity)
      * ``order_type``, ``price``, ``trigger_price``
      * ``status``, ``status_message``
      * ``order_timestamp``, ``exchange_timestamp``
      * ``average_price`` (fill avg; 0 when unfilled)
    """
    asset = f"{row['exchange']}:{row['tradingsymbol']}"
    side = OrderSide.BUY if row.get("transaction_type", "BUY").upper() == "BUY" else OrderSide.SELL
    kite_ot = str(row.get("order_type", "MARKET")).upper().replace("-", "_")
    upstream_ot = {
        "MARKET": OrderType.MARKET,
        "LIMIT": OrderType.LIMIT,
        "SL": OrderType.STOP_LIMIT,
        "SL_M": OrderType.STOP,
    }.get(kite_ot, OrderType.MARKET)
    status = _KITE_STATUS.get(str(row.get("status", "")).upper(), OrderStatus.PENDING)
    return Order(
        asset=asset,
        side=side,
        quantity=float(row.get("quantity", 0)),
        order_type=upstream_ot,
        limit_price=(float(row["price"]) if row.get("price") else None),
        stop_price=(float(row["trigger_price"]) if row.get("trigger_price") else None),
        order_id=str(row["order_id"]),
        status=status,
        filled_quantity=float(row.get("filled_quantity", 0) or 0),
        filled_price=(float(row["average_price"]) if row.get("average_price") else None),
        rejection_reason=row.get("status_message") or None,
    )


# ---- broker ------------------------------------------------------------


class KiteBroker(IndianBrokerBase):
    """Zerodha Kite implementation of :class:`IndianBrokerBase`.

    Parameters
    ----------
    client:
        An already-constructed :class:`AsyncKiteClient` (the facade that
        applies rate-limit + error translation). Constructing the client
        externally lets tests inject a fake SDK.
    """

    def __init__(self, client: AsyncKiteClient) -> None:
        self._client = client
        self._connected: bool = False

    # ---- connection lifecycle --------------------------------------

    async def connect(self) -> None:
        """Validate the access token by probing :meth:`profile`.

        Idempotent: a second call on a connected broker is a no-op. Any
        translated :class:`~ml4t.india.core.exceptions.IndiaError` from
        Kite propagates to the caller.
        """
        if self._connected:
            return
        await self._client.profile()
        self._connected = True

    async def disconnect(self) -> None:
        """Flip the local connected flag.

        Kite's REST surface has no session to tear down; websockets are
        owned separately by :class:`KiteTickerFeed`. Idempotent.
        """
        self._connected = False

    async def is_connected_async(self) -> bool:
        return self._connected

    # ---- account ---------------------------------------------------

    async def get_cash_async(self) -> float:
        """Available equity-segment cash in INR.

        Reads from ``kite.margins("equity")``; the ``available.cash``
        subtree carries the free cash after blocked margin is deducted.
        """
        data = await self._client.margins("equity")
        available = data.get("available", {}) if isinstance(data, dict) else {}
        return float(available.get("cash", 0.0))

    async def get_account_value_async(self) -> float:
        """Total account value = cash + MTM of all positions.

        MTM uses Kite's ``last_price`` on each position row multiplied by
        the signed quantity and the instrument multiplier. If ``last_price``
        is missing (happens briefly between market close and next open's
        refresh) we fall back to ``average_price`` to stay deterministic.
        """
        cash = await self.get_cash_async()
        positions = await self._client.positions()
        net_rows = positions.get("net", []) if isinstance(positions, dict) else []
        mtm = 0.0
        for row in net_rows:
            qty = float(row.get("quantity", 0))
            if qty == 0:
                continue
            mult = float(row.get("multiplier", 1) or 1)
            px = float(row.get("last_price") or row.get("average_price") or 0.0)
            mtm += qty * mult * px
        return cash + mtm

    # ---- positions -------------------------------------------------

    async def get_positions_async(self) -> dict[str, Position]:
        """Return non-zero positions keyed by ``EXCHANGE:SYMBOL``.

        Kite reports both ``day`` and ``net`` buckets. ``net`` is the one
        strategies need (cumulative across opening/closing legs); ``day``
        reflects only intraday fills and can be misleading for CNC
        delivery.
        """
        raw = await self._client.positions()
        net_rows = raw.get("net", []) if isinstance(raw, dict) else []
        out: dict[str, Position] = {}
        for row in net_rows:
            if float(row.get("quantity", 0)) == 0:
                continue
            pos = _to_position(row)
            out[pos.asset] = pos
        return out

    async def get_position_async(self, asset: str) -> Position | None:
        positions = await self.get_positions_async()
        return positions.get(asset)

    async def close_position_async(self, asset: str) -> Order | None:
        """Market-out an existing position.

        Returns ``None`` if flat. The flattening order inherits the
        position's product (CNC / MIS / NRML); the sign of the existing
        quantity dictates the close side.
        """
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
        """Place an order via Kite's ``place_order`` endpoint.

        Parameters
        ----------
        asset:
            Canonical ``EXCHANGE:SYMBOL`` (e.g. ``"NSE:RELIANCE"``).
        quantity:
            Absolute integer contract count. Negative values infer a
            SELL side when ``side`` is ``None``.
        side:
            Explicit side; when omitted, inferred from the sign of
            ``quantity``.
        order_type:
            Upstream :class:`OrderType`; translated to Kite's wire value.
        limit_price, stop_price:
            Price fields; LIMIT needs ``limit_price``, STOP / STOP_LIMIT
            need ``stop_price`` (trigger_price on Kite).
        **kwargs:
            Kite-specific: ``product`` (``CNC`` / ``MIS`` / ``NRML`` / ``MTF``),
            ``variety`` (``regular`` / ``amo`` / ``co`` / ``iceberg``),
            ``validity`` (``DAY`` / ``IOC`` / ``TTL``), ``tag``,
            ``disclosed_quantity``, ``validity_ttl``.
        """
        exchange, tradingsymbol = _split_asset(asset)

        if side is None:
            side = OrderSide.BUY if quantity >= 0 else OrderSide.SELL
        abs_qty = int(abs(quantity))
        if abs_qty == 0:
            raise InvalidInputError("quantity must be nonzero")

        kite_order_type = _ml4t_to_kite_order_type(order_type)
        kite_txn = TransactionType.BUY if side == OrderSide.BUY else TransactionType.SELL
        product = kwargs.pop("product", _default_product(exchange))
        variety = kwargs.pop("variety", Variety.REGULAR)

        payload: dict[str, Any] = dict(kwargs)
        if limit_price is not None:
            payload["price"] = limit_price
        if stop_price is not None:
            payload["trigger_price"] = stop_price

        order_id = await self._client.place_order(
            str(variety),
            tradingsymbol=tradingsymbol,
            exchange=exchange,
            transaction_type=str(kite_txn),
            quantity=abs_qty,
            product=str(product),
            order_type=str(kite_order_type),
            **payload,
        )
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

    async def cancel_order_async(
        self, order_id: str, variety: str | Variety = Variety.REGULAR
    ) -> bool:
        """Cancel an order by its broker-assigned id.

        The ``variety`` argument is required by Kite because the cancel
        endpoint is ``DELETE /orders/:variety/:order_id``. We default to
        ``regular`` -- the overwhelming majority of orders -- and let the
        caller override for CO / iceberg / AMO cancels.

        Returns ``True`` iff Kite echoed the order_id back, which
        indicates acceptance of the cancel request (not necessarily that
        the order was in a cancellable state -- Kite surfaces that via
        a :class:`~ml4t.india.core.exceptions.OrderError`).
        """
        echoed = await self._client.cancel_order(str(variety), order_id)
        return str(echoed) == str(order_id)

    async def get_pending_orders_async(self) -> list[Order]:
        """All open / trigger-pending orders (everything not terminal).

        Filters the full orderbook to rows whose upstream status maps to
        :class:`OrderStatus.PENDING`. FILLED / CANCELLED / REJECTED rows
        are excluded.
        """
        rows = await self._client.orders()
        return [
            _to_order(row)
            for row in rows
            if _KITE_STATUS.get(str(row.get("status", "")).upper(), OrderStatus.PENDING)
            == OrderStatus.PENDING
        ]


__all__ = ["KiteBroker"]

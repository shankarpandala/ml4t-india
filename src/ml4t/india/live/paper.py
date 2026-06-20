"""Library-level paper (simulated) order execution against REAL quotes.

This module is the answer to a single safety requirement: a strategy must
be able to "trade" against the real, live Kite market data **without any
possibility of a real order reaching the exchange**.

How the guarantee is enforced
------------------------------

The live order path -- the one and only place in this package that calls
Kite's ``place_order`` / ``place_autoslice_order`` -- lives in
:meth:`ml4t.india.live.kite_broker.KiteBroker.submit_order_async`. Paper
mode does **not** use that class at all. Instead it uses a *different*
concrete broker, :class:`PaperKiteBroker`, whose order path is pure local
arithmetic (:func:`simulate_fill`). Grep this file for ``place_order`` and
you will find nothing: the live call site is not reachable from the paper
code path because it is not in the paper code path.

To make that structural rather than incidental, :class:`PaperKiteBroker`
is constructed with a :class:`QuoteSource` -- a *read-only* market-data
surface. The convenience constructor :meth:`PaperKiteBroker.paper` wraps a
real :class:`~ml4t.india.kite.client.AsyncKiteClient` in
:class:`ReadOnlyQuoteClient`, which forwards only the market-data reads
(``ltp`` / ``quote`` / ``ohlc`` / ``profile`` / ``positions`` /
``margins``) and deliberately does not expose any order-mutating method.
The broker therefore holds no object on which an order could be placed.

The fill model
--------------

The default fill model (:class:`LastPriceFillModel`) fills at the real
last-traded price read from the live client, optionally widened by a
slippage allowance, and then charges the trade through
:class:`~ml4t.india.backtest.charges.ZerodhaChargesModel`. The fill model
is overridable -- pass any callable matching :class:`FillModel`.

The pure fill arithmetic (:func:`simulate_fill`) takes explicit price +
order inputs and returns a :class:`Fill`; it performs no I/O, so it is
unit-tested deterministically without any credentials (see
``tests/unit/test_paper_execution.py``).
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Callable
from dataclasses import dataclass, replace
from typing import Any, Protocol, runtime_checkable

from ml4t.backtest.types import Order, OrderSide, OrderStatus, OrderType, Position

from ml4t.india.backtest.charges import IndianChargesModel, ZerodhaChargesModel
from ml4t.india.core.exceptions import InvalidInputError
from ml4t.india.live.base import IndianBrokerBase

# ---------------------------------------------------------------------------
# Fill arithmetic (pure -- no I/O, deterministically unit-tested).
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Fill:
    """One simulated execution.

    All money fields are in INR. ``quantity`` is always a positive
    magnitude; the trade direction lives in ``side``.

    Attributes
    ----------
    asset:
        Canonical ``EXCHANGE:SYMBOL`` the order was for.
    side:
        ``BUY`` or ``SELL``.
    quantity:
        Filled magnitude (always > 0).
    fill_price:
        Execution price per unit *after* the fill model's slippage
        adjustment.
    reference_price:
        The real quote price the fill was simulated against (before
        slippage). Kept so callers can audit the slippage component.
    charges:
        Total statutory + brokerage charges for this fill, from the
        charges model (sign-aware: STT is sell-side, stamp is buy-side).
    slippage:
        Total slippage cost in INR (``|fill_price - reference_price|`` *
        ``quantity``); always >= 0.
    gross_value:
        ``quantity * fill_price`` (turnover, ignoring charges).
    cash_delta:
        Signed change to account cash including charges -- negative for a
        buy (cash leaves), positive for a sell (cash arrives, net of
        charges).
    """

    asset: str
    side: OrderSide
    quantity: float
    fill_price: float
    reference_price: float
    charges: float
    slippage: float
    gross_value: float
    cash_delta: float


@runtime_checkable
class FillModel(Protocol):
    """Maps a real reference (quote) price to an execution price per unit."""

    def __call__(
        self,
        *,
        side: OrderSide,
        quantity: float,
        reference_price: float,
    ) -> float: ...


@dataclass(frozen=True, slots=True)
class LastPriceFillModel:
    """Fill at the real last-traded price, optionally widened by slippage.

    ``slippage_bps`` is a one-sided allowance in basis points: a buyer
    pays ``reference_price * (1 + slippage_bps/1e4)`` and a seller
    receives ``reference_price * (1 - slippage_bps/1e4)``. The default of
    zero fills exactly at the quote, which is the brief's default.
    """

    slippage_bps: float = 0.0

    def __call__(
        self,
        *,
        side: OrderSide,
        quantity: float,  # noqa: ARG002 -- part of the FillModel contract
        reference_price: float,
    ) -> float:
        adj = reference_price * (self.slippage_bps / 10_000.0)
        return reference_price + adj if side is OrderSide.BUY else reference_price - adj


def simulate_fill(
    *,
    asset: str,
    side: OrderSide,
    quantity: float,
    reference_price: float,
    charges_model: IndianChargesModel | None = None,
    fill_model: FillModel | None = None,
) -> Fill:
    """Compute a deterministic :class:`Fill` from explicit inputs.

    This is the pure heart of paper execution: no network, no clock, no
    randomness. Given a side, magnitude, a real reference price, and the
    charge/fill models, it returns exactly what a paper broker would book.

    Parameters
    ----------
    asset:
        ``EXCHANGE:SYMBOL``; the charges model parses the prefix to pick
        the statutory schedule (equity vs F&O vs currency vs commodity).
    side:
        ``BUY`` or ``SELL``.
    quantity:
        Positive magnitude. Zero / negative raises
        :class:`InvalidInputError` -- the *sign* of a trade is ``side``,
        not ``quantity``.
    reference_price:
        The real quote price (> 0).
    charges_model:
        Defaults to :class:`ZerodhaChargesModel`.
    fill_model:
        Defaults to :class:`LastPriceFillModel` (fill at the quote).

    Returns
    -------
    Fill
    """
    if quantity <= 0:
        raise InvalidInputError(f"quantity must be a positive magnitude (got {quantity!r})")
    if reference_price <= 0:
        raise InvalidInputError(f"reference_price must be > 0 (got {reference_price!r})")

    charges_model = charges_model or ZerodhaChargesModel()
    fill_model = fill_model or LastPriceFillModel()

    fill_price = float(fill_model(side=side, quantity=quantity, reference_price=reference_price))
    if fill_price <= 0:
        raise InvalidInputError(
            f"fill model produced a non-positive price ({fill_price!r}); check slippage_bps"
        )

    # Charges are sign-aware: a sell passes a negative quantity so the
    # model applies STT (sell-side) rather than stamp duty (buy-side).
    signed_qty = quantity if side is OrderSide.BUY else -quantity
    charges = float(charges_model.calculate(asset, signed_qty, fill_price))

    gross = quantity * fill_price
    slippage = abs(fill_price - reference_price) * quantity
    cash_delta = (-gross - charges) if side is OrderSide.BUY else (gross - charges)

    return Fill(
        asset=asset,
        side=side,
        quantity=quantity,
        fill_price=fill_price,
        reference_price=reference_price,
        charges=charges,
        slippage=slippage,
        gross_value=gross,
        cash_delta=cash_delta,
    )


# ---------------------------------------------------------------------------
# Read-only market-data surface (the structural barrier).
# ---------------------------------------------------------------------------


@runtime_checkable
class QuoteSource(Protocol):
    """The *only* capability a paper broker needs from a live client.

    Note what is absent: there is no ``place_order`` / ``modify_order`` /
    ``cancel_order`` in this protocol. A :class:`PaperKiteBroker` typed on
    this surface cannot reach an order endpoint.
    """

    async def ltp(self, instruments: list[str]) -> dict[str, dict[str, Any]]: ...

    async def profile(self) -> dict[str, Any]: ...


class ReadOnlyQuoteClient:
    """A read-only adapter over :class:`AsyncKiteClient`.

    Forwards market-data reads and account *reads* only. It intentionally
    does not define -- and does not forward -- ``place_order``,
    ``place_autoslice_order``, ``modify_order`` or ``cancel_order``. This
    is what lets :class:`PaperKiteBroker` hold a real client connection
    while being structurally incapable of sending a live order through it.
    """

    def __init__(self, client: Any) -> None:
        # Stored privately. The public method set below is the entire
        # surface a paper broker is given; none of it mutates the order
        # book at the exchange.
        self._client = client

    async def ltp(self, instruments: list[str]) -> dict[str, dict[str, Any]]:
        return await self._client.ltp(instruments)

    async def quote(self, instruments: list[str]) -> dict[str, dict[str, Any]]:
        return await self._client.quote(instruments)

    async def ohlc(self, instruments: list[str]) -> dict[str, dict[str, Any]]:
        return await self._client.ohlc(instruments)

    async def profile(self) -> dict[str, Any]:
        return await self._client.profile()

    async def margins(self, segment: str | None = None) -> dict[str, Any]:
        return await self._client.margins(segment)

    async def positions(self) -> dict[str, list[dict[str, Any]]]:
        return await self._client.positions()


# ---------------------------------------------------------------------------
# Paper broker.
# ---------------------------------------------------------------------------


Clock = Callable[[], dt.datetime]


class PaperKiteBroker(IndianBrokerBase):
    """Paper-execution broker: real quotes in, simulated fills out.

    Implements the full :class:`IndianBrokerBase` contract, but every
    order is filled locally via :func:`simulate_fill` against the real
    last-traded price read from :attr:`QuoteSource`. It books cash and
    positions in memory; it never contacts an order endpoint.

    Parameters
    ----------
    quotes:
        A read-only :class:`QuoteSource`. Use :meth:`paper` to build one
        from a live :class:`~ml4t.india.kite.client.AsyncKiteClient`.
    charges_model:
        Charge schedule applied to each fill. Defaults to
        :class:`~ml4t.india.backtest.charges.ZerodhaChargesModel`.
    fill_model:
        Maps the real reference price to an execution price. Defaults to
        :class:`LastPriceFillModel` (fill at the quote, zero slippage).
    starting_cash:
        Opening simulated cash balance in INR.
    clock:
        Zero-arg callable returning the "now" stamped on fills/positions;
        injectable for deterministic tests. Defaults to UTC wall clock.
    """

    def __init__(
        self,
        quotes: QuoteSource,
        *,
        charges_model: IndianChargesModel | None = None,
        fill_model: FillModel | None = None,
        starting_cash: float = 0.0,
        clock: Clock | None = None,
    ) -> None:
        self._quotes = quotes
        self._charges = charges_model or ZerodhaChargesModel()
        self._fill_model = fill_model or LastPriceFillModel()
        self._starting_cash = float(starting_cash)
        self._cash = float(starting_cash)
        self._positions: dict[str, Position] = {}
        self._orders: dict[str, Order] = {}
        self._fills: list[Fill] = []
        self._connected = False
        self._seq = 0
        self._clock: Clock = clock or (lambda: dt.datetime.now(dt.UTC))

    @classmethod
    def paper(
        cls,
        client: Any,
        *,
        charges_model: IndianChargesModel | None = None,
        fill_model: FillModel | None = None,
        starting_cash: float = 0.0,
        clock: Clock | None = None,
    ) -> PaperKiteBroker:
        """Build a paper broker from a live :class:`AsyncKiteClient`.

        The client is wrapped in :class:`ReadOnlyQuoteClient` so the broker
        receives only the market-data surface. Quotes are real; execution
        is simulated.
        """
        return cls(
            ReadOnlyQuoteClient(client),
            charges_model=charges_model,
            fill_model=fill_model,
            starting_cash=starting_cash,
            clock=clock,
        )

    # ---- introspection (paper-only extras) -------------------------

    @property
    def fills(self) -> list[Fill]:
        """All simulated fills, in execution order."""
        return list(self._fills)

    @property
    def realized_cash(self) -> float:
        """Current simulated cash balance in INR."""
        return self._cash

    # ---- connection lifecycle --------------------------------------

    async def connect(self) -> None:
        """Validate the underlying session via a read-only ``profile`` probe.

        Mirrors :meth:`KiteBroker.connect` so paper and live wiring are
        interchangeable, but the probe is a pure read -- it never sends an
        order. Idempotent.
        """
        if self._connected:
            return
        await self._quotes.profile()
        self._connected = True

    async def disconnect(self) -> None:
        self._connected = False

    async def is_connected_async(self) -> bool:
        return self._connected

    # ---- account ---------------------------------------------------

    async def get_cash_async(self) -> float:
        return self._cash

    async def get_account_value_async(self) -> float:
        """Cash + marked-to-market value of all simulated positions.

        Positions are marked at their last simulated fill price
        (``current_price``); this keeps account value deterministic and
        free of extra network reads. Callers wanting a live mark can call
        :meth:`mark_to_market` first.
        """
        mtm = 0.0
        for pos in self._positions.values():
            px = pos.current_price if pos.current_price is not None else pos.entry_price
            mtm += pos.quantity * pos.multiplier * px
        return self._cash + mtm

    # ---- positions -------------------------------------------------

    async def get_positions_async(self) -> dict[str, Position]:
        return dict(self._positions)

    async def get_position_async(self, asset: str) -> Position | None:
        return self._positions.get(asset)

    async def close_position_async(self, asset: str) -> Order | None:
        position = self._positions.get(asset)
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
        """Simulate an immediate fill against the real last-traded price.

        Reads the live LTP for ``asset`` through the read-only quote
        surface, prices the fill via the fill model, charges it via the
        charges model, and books the resulting cash + position change in
        memory. The returned :class:`Order` is already ``FILLED``.

        No live order endpoint is contacted at any point. ``**kwargs``
        (e.g. ``product``, ``variety``) are accepted for signature
        compatibility with :class:`KiteBroker` but do not change the
        simulated economics.
        """
        del kwargs  # accepted for KiteBroker signature parity; not priced
        if ":" not in asset:
            raise InvalidInputError(f"asset must be 'EXCHANGE:SYMBOL' (got {asset!r})")
        if side is None:
            side = OrderSide.BUY if quantity >= 0 else OrderSide.SELL
        magnitude = abs(float(quantity))
        if magnitude == 0:
            raise InvalidInputError("quantity must be nonzero")

        reference_price = await self._reference_price(asset, order_type, limit_price)
        fill = simulate_fill(
            asset=asset,
            side=side,
            quantity=magnitude,
            reference_price=reference_price,
            charges_model=self._charges,
            fill_model=self._fill_model,
        )
        self._book_fill(fill)

        order_id = self._next_order_id()
        order = Order(
            asset=asset,
            side=side,
            quantity=magnitude,
            order_type=order_type,
            limit_price=limit_price,
            stop_price=stop_price,
            order_id=order_id,
            status=OrderStatus.FILLED,
            filled_price=fill.fill_price,
            filled_quantity=magnitude,
            filled_at=self._clock(),
        )
        self._orders[order_id] = order
        return order

    async def cancel_order_async(self, order_id: str) -> bool:
        """Paper orders fill instantly, so there is never anything to cancel.

        Returns ``False`` for any id (the order, if it exists, is already
        terminal). Present to satisfy the broker contract.
        """
        del order_id
        return False

    async def replace_order_async(
        self,
        order_id: str,
        quantity: float | None = None,
        limit_price: float | None = None,
        stop_price: float | None = None,
        **kwargs: Any,
    ) -> Order:
        """Unsupported in paper mode -- fills are immediate and terminal.

        Raises :class:`InvalidInputError`, matching the base-class
        contract that brokers reject unsupported transitions.
        """
        del order_id, quantity, limit_price, stop_price, kwargs
        raise InvalidInputError(
            "paper orders fill immediately and cannot be modified; "
            "submit a new order instead"
        )

    async def get_pending_orders_async(self) -> list[Order]:
        """Always empty: every paper order fills on submission."""
        return []

    # ---- helpers ---------------------------------------------------

    async def mark_to_market(self) -> None:
        """Refresh each open position's ``current_price`` from a live LTP.

        Optional -- :meth:`get_account_value_async` works off the last
        fill price without it. Useful before reporting account value at a
        point in time well after the last trade.
        """
        if not self._positions:
            return
        assets = list(self._positions)
        data = await self._quotes.ltp(assets)
        for asset in assets:
            entry = _extract_ltp(data, asset)
            if entry is None:
                continue
            self._positions[asset] = replace(self._positions[asset], current_price=entry)

    async def _reference_price(
        self,
        asset: str,
        order_type: OrderType,
        limit_price: float | None,
    ) -> float:
        """Real last-traded price to simulate against.

        Market orders price at the live LTP. A LIMIT order with a price is
        assumed marketable and prices at the limit (the conservative
        interpretation for a fill that, by construction, happened).
        """
        if order_type is OrderType.LIMIT and limit_price is not None:
            return float(limit_price)
        data = await self._quotes.ltp([asset])
        price = _extract_ltp(data, asset)
        if price is None:
            raise InvalidInputError(
                f"no last-traded price available for {asset!r}; cannot simulate a fill"
            )
        return price

    def _book_fill(self, fill: Fill) -> None:
        """Apply a fill to cash + the netted position book."""
        self._cash += fill.cash_delta
        self._fills.append(fill)

        signed = fill.quantity if fill.side is OrderSide.BUY else -fill.quantity
        existing = self._positions.get(fill.asset)
        if existing is None:
            self._positions[fill.asset] = Position(
                asset=fill.asset,
                quantity=signed,
                entry_price=fill.fill_price,
                entry_time=self._clock(),
                current_price=fill.fill_price,
                entry_commission=fill.charges,
            )
            return

        new_qty = existing.quantity + signed
        if new_qty == 0:
            del self._positions[fill.asset]
            return

        # Re-average the entry only when adding to the same side; reducing
        # or flipping keeps the surviving leg's entry price.
        same_side = (existing.quantity > 0) == (signed > 0)
        if same_side:
            entry_price = (
                existing.entry_price * existing.quantity + fill.fill_price * signed
            ) / new_qty
        else:
            entry_price = existing.entry_price
        self._positions[fill.asset] = replace(
            existing,
            quantity=new_qty,
            entry_price=entry_price,
            current_price=fill.fill_price,
        )

    def _next_order_id(self) -> str:
        self._seq += 1
        return f"PAPER-{self._seq:06d}"


def _extract_ltp(data: dict[str, Any], asset: str) -> float | None:
    """Pull ``last_price`` out of a Kite ``ltp`` payload for ``asset``.

    Kite keys ``ltp`` responses by the requested instrument string, so we
    try ``asset`` first and fall back to the sole row when only one was
    requested (Kite occasionally normalises the key casing).
    """
    if not isinstance(data, dict):
        return None
    entry = data.get(asset)
    if entry is None and len(data) == 1:
        entry = next(iter(data.values()))
    if not isinstance(entry, dict):
        return None
    price = entry.get("last_price")
    if price is None:
        return None
    return float(price)


__all__ = [
    "Fill",
    "FillModel",
    "LastPriceFillModel",
    "PaperKiteBroker",
    "QuoteSource",
    "ReadOnlyQuoteClient",
    "simulate_fill",
]

"""India-layer abstract base for live brokers.

:class:`IndianBrokerBase` is an abstract class that structurally satisfies
:class:`ml4t.live.protocols.AsyncBrokerProtocol`, and adds a small set of
Indian-market concerns in one place. Concrete brokers (``KiteBroker`` in
Phase 2; future ``UpstoxBroker``, ``AngelOneBroker``, ``FivePaisaBroker``)
subclass this instead of implementing the protocol from scratch each time.

Why this layer exists
---------------------

Upstream does NOT expose a concrete broker base class. ``AlpacaBroker`` and
``IBBroker`` each implement :class:`~ml4t.live.protocols.AsyncBrokerProtocol`
directly, with no shared ancestry. That is appropriate for their needs,
but it means that Indian cross-broker concerns (product mapping, lot
rounding, charges wiring) would have to be duplicated on every concrete
Indian broker if we followed the same pattern.

This abstract base consolidates those concerns. A Phase-0 commit only
declares the signatures -- concrete behaviour (KiteClient injection,
order translation, symbol resolution) lands in Phase 2 with
:class:`KiteBroker` and in subsequent phases with additional brokers.

Protocol conformance
--------------------

This class does not inherit from
:class:`~ml4t.live.protocols.AsyncBrokerProtocol`. Protocols use
structural typing (``@runtime_checkable`` or ``isinstance`` checks via
``__subclasshook__``); we declare every required method as abstract,
which gives concrete subclasses both a conformance guarantee and a
python ``abc`` enforced contract.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

from ml4t.backtest.types import Order, OrderSide, OrderType, Position


class IndianBrokerBase(ABC):
    """Abstract async broker for Indian markets.

    Every concrete subclass MUST implement every abstract method below.
    Concrete subclasses SHOULD NOT need to override anything beyond the
    abstract set; cross-broker helpers land here as non-abstract methods
    in subsequent phases.
    """

    # ---- connection lifecycle --------------------------------------

    @abstractmethod
    async def connect(self) -> None:
        """Open a broker session.

        Concrete implementations: refresh the access token, open any
        HTTP client pool, bring up the order-postback websocket, and
        prime internal caches. Idempotent: calling on an already-open
        session is a no-op.
        """

    @abstractmethod
    async def disconnect(self) -> None:
        """Close the broker session cleanly.

        Concrete implementations: drain pending tasks, close sockets,
        flush local order cache to disk if applicable. Idempotent.
        """

    @abstractmethod
    async def is_connected_async(self) -> bool:
        """Return ``True`` iff a live broker session is currently open."""

    # ---- account ---------------------------------------------------

    @abstractmethod
    async def get_account_value_async(self) -> float:
        """Total account value in INR: cash + MTM of all positions."""

    @abstractmethod
    async def get_cash_async(self) -> float:
        """Available cash in INR (excludes collateral / blocked margin)."""

    # ---- positions -------------------------------------------------

    @abstractmethod
    async def get_position_async(self, asset: str) -> Position | None:
        """Return the open position for ``asset``, or ``None`` if flat."""

    @abstractmethod
    async def get_positions_async(self) -> dict[str, Position]:
        """Return all open positions keyed by asset symbol."""

    @abstractmethod
    async def close_position_async(self, asset: str) -> Order | None:
        """Submit a market order to flatten the position in ``asset``.

        Returns the resulting order, or ``None`` if the position was
        already flat.
        """

    # ---- orders ----------------------------------------------------

    @abstractmethod
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
        """Place an order; broker-specific kwargs go through ``**kwargs``.

        India-specific kwargs that concrete subclasses honour include
        ``product`` (CNC / MIS / NRML / MTF), ``variety`` (regular /
        amo / co / iceberg), and ``validity`` (DAY / IOC / TTL).
        """

    @abstractmethod
    async def cancel_order_async(self, order_id: str) -> bool:
        """Cancel the order with the given broker-assigned ``order_id``.

        Returns ``True`` if the cancel was accepted by the broker.
        """

    @abstractmethod
    async def get_pending_orders_async(self) -> list[Order]:
        """Return orders that are open / pending execution."""


__all__ = ["IndianBrokerBase"]

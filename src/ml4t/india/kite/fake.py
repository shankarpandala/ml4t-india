"""In-memory test double for :class:`kiteconnect.KiteConnect`.

:class:`FakeKiteClient` is a deterministic stand-in for the Zerodha Kite
SDK. Every concrete provider / broker / feed in ml4t-india will eventually
depend on a :class:`~ml4t.india.kite.client.KiteClient` facade (introduced
in Phase 1) that wraps either a real ``kiteconnect.KiteConnect`` or an
instance of this fake.

Design goals (Phase-0)
----------------------

1. Zero network. Every method returns canned data from an in-memory store
   or raises a pre-configured exception.

2. Method-call recording. Tests can inspect :attr:`FakeKiteClient.calls`
   to assert the adapter wired the right parameters into the right
   method.

3. Broker-shape compatible. The public methods mirror the subset of
   :class:`kiteconnect.KiteConnect` that ml4t-india actually uses --
   historical candles, quotes, order placement / cancel / history,
   positions, margins, instruments dump. New methods will be added as
   Phase 2+ code starts touching them.

4. Error injection. :meth:`set_next_error` queues an exception for the
   next call to any method, so tests can exercise error paths (token
   expired, rate limit hit, input rejected) without fiddling with the
   canned-data store.

Design non-goals
----------------

* NOT a substitute for recorded Kite HTTP cassettes. This fake is for
  unit-level isolation -- wire-format compatibility belongs to cassette
  tests (``vcrpy`` / ``respx``) added in Phase 3.

* NOT auto-generated from the SDK. Keeping the surface explicit makes it
  obvious when adapter code starts depending on a new Kite API method.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from typing import Any


@dataclass
class RecordedCall:
    """A single method call made against the fake, captured for assertion.

    Attributes
    ----------
    method:
        Name of the fake method invoked (e.g. ``"historical_data"``).
    args:
        Positional arguments the caller passed.
    kwargs:
        Keyword arguments the caller passed.
    """

    method: str
    args: tuple[Any, ...] = field(default_factory=tuple)
    kwargs: dict[str, Any] = field(default_factory=dict)


class FakeKiteClient:
    """In-memory test double for Zerodha ``kiteconnect.KiteConnect``.

    Parameters
    ----------
    api_key:
        Matches the ``kiteconnect.KiteConnect(api_key=...)`` signature.
        Stored verbatim; tests can assert on it.
    access_token:
        Pre-seeded access token. If ``None``, calls that require auth
        will raise a placeholder error (Phase-0 does not enforce this
        yet; Phase 1 adds the real check).
    """

    def __init__(
        self,
        api_key: str = "fake-api-key",
        access_token: str | None = None,
    ) -> None:
        self.api_key = api_key
        self.access_token = access_token

        # Canned data stores. Tests seed these via the setter methods
        # below; methods read from them and return copies so tests cannot
        # accidentally mutate shared state.
        self._historical: dict[str, list[dict[str, Any]]] = {}
        self._instruments: list[dict[str, Any]] = []
        self._quotes: dict[str, dict[str, Any]] = {}
        self._orders: list[dict[str, Any]] = []
        self._positions: list[dict[str, Any]] = []
        self._margins: dict[str, Any] = {}

        # Sequence of exceptions to raise on the NEXT N calls (any
        # method). Popped FIFO. If empty, methods return canned data
        # normally.
        self._next_errors: deque[Exception] = deque()

        # Every method invocation is appended here for test assertion.
        self.calls: list[RecordedCall] = []

    # ---- test setup helpers --------------------------------------

    def set_historical_data(
        self, instrument_token: str, candles: list[dict[str, Any]]
    ) -> None:
        """Seed canned historical candles for one instrument."""
        self._historical[str(instrument_token)] = list(candles)

    def set_instruments(self, instruments: list[dict[str, Any]]) -> None:
        """Seed the instruments dump returned by :meth:`instruments`."""
        self._instruments = list(instruments)

    def set_quote(self, symbol: str, quote: dict[str, Any]) -> None:
        """Seed a canned quote payload for one symbol."""
        self._quotes[symbol] = dict(quote)

    def set_margins(self, margins: dict[str, Any]) -> None:
        """Seed the margins payload returned by :meth:`margins`."""
        self._margins = dict(margins)

    def set_next_error(self, exc: Exception) -> None:
        """Queue an exception to raise on the next method call.

        Multiple errors queue FIFO; each is consumed by one call.
        """
        self._next_errors.append(exc)

    # ---- kiteconnect-shaped public API ---------------------------
    #
    # These signatures match kiteconnect.KiteConnect closely enough
    # that the Phase 1 KiteClient facade (or ad-hoc test code) can
    # swap in FakeKiteClient for the real SDK instance.

    def historical_data(
        self,
        instrument_token: int | str,
        from_date: Any,
        to_date: Any,
        interval: str,
        continuous: bool = False,
        oi: bool = False,
    ) -> list[dict[str, Any]]:
        """Return canned candles for ``instrument_token``; empty if unseeded."""
        self._record(
            "historical_data",
            instrument_token,
            from_date,
            to_date,
            interval,
            continuous=continuous,
            oi=oi,
        )
        return list(self._historical.get(str(instrument_token), []))

    def instruments(self, exchange: str | None = None) -> list[dict[str, Any]]:
        """Return canned instruments dump, filtered by ``exchange`` if given."""
        self._record("instruments", exchange=exchange)
        if exchange is None:
            return list(self._instruments)
        return [i for i in self._instruments if i.get("exchange") == exchange]

    def quote(self, instruments: list[str]) -> dict[str, dict[str, Any]]:
        """Return canned quotes keyed by ``exchange:tradingsymbol``."""
        self._record("quote", instruments)
        return {k: dict(v) for k, v in self._quotes.items() if k in instruments}

    def ltp(self, instruments: list[str]) -> dict[str, dict[str, Any]]:
        """Return canned LTPs; payload mirrors Kite's LTP-only response."""
        self._record("ltp", instruments)
        return {
            k: {"last_price": v.get("last_price", 0.0)}
            for k, v in self._quotes.items()
            if k in instruments
        }

    def place_order(
        self,
        variety: str,
        *,
        tradingsymbol: str,
        exchange: str,
        transaction_type: str,
        quantity: int,
        product: str,
        order_type: str,
        **kwargs: Any,
    ) -> str:
        """Append an order to the in-memory book and return an order id."""
        self._record(
            "place_order",
            variety,
            tradingsymbol=tradingsymbol,
            exchange=exchange,
            transaction_type=transaction_type,
            quantity=quantity,
            product=product,
            order_type=order_type,
            **kwargs,
        )
        order_id = f"FAKE-{len(self._orders) + 1:06d}"
        self._orders.append(
            {
                "order_id": order_id,
                "variety": variety,
                "status": "COMPLETE",
                "tradingsymbol": tradingsymbol,
                "exchange": exchange,
                "transaction_type": transaction_type,
                "quantity": quantity,
                "product": product,
                "order_type": order_type,
                **kwargs,
            }
        )
        return order_id

    def cancel_order(self, variety: str, order_id: str, **kwargs: Any) -> str:
        """Mark a fake order as cancelled; returns the order id."""
        self._record("cancel_order", variety, order_id, **kwargs)
        for order in self._orders:
            if order["order_id"] == order_id:
                order["status"] = "CANCELLED"
                break
        return order_id

    def orders(self) -> list[dict[str, Any]]:
        """Return a copy of the in-memory order book."""
        self._record("orders")
        return [dict(o) for o in self._orders]

    def positions(self) -> dict[str, list[dict[str, Any]]]:
        """Return positions in Kite's ``net`` / ``day`` shape."""
        self._record("positions")
        snapshot = [dict(p) for p in self._positions]
        return {"net": snapshot, "day": snapshot}

    def margins(self, segment: str | None = None) -> dict[str, Any]:
        """Return the canned margins payload."""
        self._record("margins", segment=segment)
        if segment is None:
            return dict(self._margins)
        return dict(self._margins.get(segment, {}))

    def profile(self) -> dict[str, Any]:
        """Return a fixed user profile; useful for smoke-testing auth paths."""
        self._record("profile")
        return {
            "user_id": "FAKE001",
            "user_name": "Fake User",
            "broker": "ZERODHA",
            "exchanges": ["NSE", "BSE", "NFO", "BFO", "CDS", "BCD", "MCX"],
        }

    # ---- internals -----------------------------------------------

    def _record(self, method: str, *args: Any, **kwargs: Any) -> None:
        """Append the call to :attr:`calls`; raise any queued error first."""
        self.calls.append(
            RecordedCall(method=method, args=args, kwargs=dict(kwargs))
        )
        if self._next_errors:
            raise self._next_errors.popleft()


__all__ = ["FakeKiteClient", "RecordedCall"]

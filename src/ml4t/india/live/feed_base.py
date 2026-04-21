"""India-layer abstract base for market-data ticker feeds.

:class:`IndianTickerFeedBase` structurally satisfies
:class:`ml4t.live.protocols.DataFeedProtocol` -- a minimal interface of
``start()`` (async) and ``stop()`` (sync). Every concrete Indian ticker
feed (``KiteTickerFeed`` in Phase 4 and future broker feeds) subclasses
this.

The protocol is small because actual tick dispatch happens via the
concrete feed's subscription + callback machinery, not through the
protocol surface. Each broker has its own wire format (Kite: binary
ltp/quote/full; Upstox: protobuf; Angel One: binary), so the serialisation
hook cannot usefully live here. What IS shared and lives in this base:

* Subscription lifecycle: start, stop.
* India-specific concerns (segment-aware dedup, resubscribe on reconnect,
  per-connection instrument cap) -- added in later phases as concrete
  subclasses need them.

The ``start`` / ``stop`` sync-async asymmetry mirrors upstream exactly:
upstream's protocol declares ``start`` as ``async`` and ``stop`` as
synchronous. Matching that signature is load-bearing for structural
subtyping.
"""

from __future__ import annotations

from abc import ABC, abstractmethod


class IndianTickerFeedBase(ABC):
    """Abstract ticker feed for Indian markets.

    Concrete subclasses MUST implement :meth:`start` and :meth:`stop`.
    Everything else (subscription management, reconnection, binary
    decoding) is broker-specific and lives on the concrete class.
    """

    @abstractmethod
    async def start(self) -> None:
        """Connect to the broker streaming endpoint and begin receiving ticks.

        Implementations typically: open the websocket, authenticate,
        subscribe to the configured instrument list, and spawn a
        background task that drains incoming frames into the caller's
        consumer queue.
        """

    @abstractmethod
    def stop(self) -> None:
        """Signal the feed to stop and close its streaming socket.

        Synchronous by design to match upstream's ``DataFeedProtocol``.
        Idempotent: calling ``stop`` on an already-stopped feed is a
        no-op.
        """


__all__ = ["IndianTickerFeedBase"]

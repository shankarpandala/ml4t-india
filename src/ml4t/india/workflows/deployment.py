"""Deployment pipeline: wire broker + feed + postback handler to a strategy.

Live-trading scaffolding. Not a strategy engine -- just the plumbing
that connects Indian-market adapters to a caller-supplied strategy
object. The caller still owns:

* Signal generation (on_tick handler logic).
* Order sizing / lot rounding.
* Risk enforcement (stop-loss, max daily loss, etc.).

What this class does:

* Own the (KiteBroker, KiteTickerFeed, PostbackHandler) triple.
* Call :meth:`KiteBroker.connect` + :meth:`KiteTickerFeed.start` in
  the right order.
* Subscribe the configured instrument tokens.
* Route ``on_ticks`` / ``on_order`` events to the strategy.
* Clean up on :meth:`stop`.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Any

from ml4t.india.live.kite_broker import KiteBroker
from ml4t.india.live.kite_ticker_feed import KiteTickerFeed, TickMode
from ml4t.india.live.postbacks import PostbackHandler


@dataclass
class DeploymentPipelineState:
    """Lifecycle state so callers can assert where they are.

    Deliberately minimal -- it's ``started`` plus the instrument list
    we asked the feed to subscribe. Observability beyond that belongs
    to the caller's metrics stack.
    """

    started: bool = False
    subscribed_tokens: list[int] = field(default_factory=list)


class DeploymentPipeline:
    """Live-trading wiring for an Indian-market strategy.

    Parameters
    ----------
    broker:
        Already-constructed :class:`KiteBroker`.
    feed:
        Already-constructed :class:`KiteTickerFeed`. Construction order
        matters: the feed must be created BEFORE start() so tests can
        install fakes.
    postbacks:
        Optional :class:`PostbackHandler`. If provided, the strategy's
        ``on_order`` handler (if it exists) is registered on it.
    strategy:
        An arbitrary object. The pipeline calls ``strategy.on_tick(ticks)``
        and ``strategy.on_order(order)`` if those attributes exist;
        absent callbacks are silently skipped. Keeping the contract
        duck-typed avoids a rigid base class for something that varies
        wildly across research frameworks.
    instrument_tokens:
        Subscription set. Forwarded to :meth:`KiteTickerFeed.subscribe`
        on :meth:`start`.
    subscription_mode:
        ``"ltp"`` / ``"quote"`` / ``"full"``. Defaults to ``"quote"``
        (matches :class:`KiteTickerFeed`'s default).
    """

    def __init__(
        self,
        broker: KiteBroker,
        feed: KiteTickerFeed,
        strategy: Any,
        instrument_tokens: Iterable[int],
        postbacks: PostbackHandler | None = None,
        subscription_mode: TickMode = "quote",
    ) -> None:
        self._broker = broker
        self._feed = feed
        self._postbacks = postbacks
        self._strategy = strategy
        self._tokens: list[int] = list(instrument_tokens)
        self._mode: TickMode = subscription_mode
        self.state = DeploymentPipelineState()

    # ---- lifecycle --------------------------------------------------

    async def start(self) -> None:
        """Connect the broker, start the feed, subscribe, wire handlers.

        Idempotent: a second call is a no-op.
        """
        if self.state.started:
            return

        # 1) Broker first -- catches auth problems before we start
        #    subscribing to a feed we couldn't trade into anyway.
        await self._broker.connect()

        # 2) Wire handlers BEFORE start() so we don't miss ticks that
        #    arrive between the websocket open and our subscribe() call
        #    (Kite can send cached prices immediately on connect).
        on_tick = getattr(self._strategy, "on_tick", None)
        if callable(on_tick):
            self._feed.on_ticks(on_tick)
        on_order = getattr(self._strategy, "on_order", None)
        if callable(on_order) and self._postbacks is not None:
            self._postbacks.on_order(on_order)

        # 3) Start the feed first so subscribe() pushes straight to the
        #    live ticker. (KiteTickerFeed also replays on the broker's
        #    on_connect event, so pre-start subscribe is also safe --
        #    but we prefer the explicit push since the first on_connect
        #    races with our socket-open handshake.)
        await self._feed.start()
        self._feed.subscribe(self._tokens, mode=self._mode)

        self.state.started = True
        self.state.subscribed_tokens = list(self._tokens)

    async def stop(self) -> None:
        """Stop the feed then disconnect the broker. Idempotent."""
        if not self.state.started:
            return
        self._feed.stop()
        await self._broker.disconnect()
        self.state.started = False


__all__ = ["DeploymentPipeline", "DeploymentPipelineState"]

"""Tests for :class:`ml4t.india.workflows.deployment.DeploymentPipeline`."""

from __future__ import annotations

from typing import Any

import pytest

from ml4t.india.kite.client import AsyncKiteClient, KiteClient
from ml4t.india.kite.fake import FakeKiteClient
from ml4t.india.kite.rate_limit import KiteRateLimiter
from ml4t.india.live.kite_broker import KiteBroker
from ml4t.india.live.kite_ticker_feed import KiteTickerFeed
from ml4t.india.live.postbacks import PostbackHandler
from ml4t.india.workflows import DeploymentPipeline


class FakeTicker:
    """Same shape as the ticker-feed test fake."""

    on_ticks = on_connect = on_close = on_error = None

    def __init__(self, api_key: str, access_token: str) -> None:
        self.subscribed: list[list[int]] = []
        self.modes: list[tuple[str, list[int]]] = []
        self.connected = False
        self.closed = False

    def connect(self, threaded: bool = True) -> None:
        self.connected = True

    def close(self, code: int | None = None, reason: str | None = None) -> None:
        self.closed = True

    def subscribe(self, tokens: list[int]) -> None:
        self.subscribed.append(list(tokens))

    def unsubscribe(self, tokens: list[int]) -> None: ...

    def set_mode(self, mode: str, tokens: list[int]) -> None:
        self.modes.append((mode, list(tokens)))

    def is_connected(self) -> bool:
        return self.connected

    def emit_ticks(self, ticks: list[dict[str, Any]]) -> None:
        if self.on_ticks is not None:
            self.on_ticks(self, ticks)


def _fast_limiter() -> KiteRateLimiter:
    return KiteRateLimiter(
        limits={"quote": 500, "historical": 500, "orders": 500, "other": 500},
        global_rate=500.0,
    )


@pytest.fixture
def broker() -> KiteBroker:
    sdk = FakeKiteClient("k", "a")
    sync = KiteClient(sdk=sdk, rate_limiter=_fast_limiter())
    return KiteBroker(AsyncKiteClient(sync))


@pytest.fixture
def feed() -> tuple[KiteTickerFeed, dict[str, FakeTicker]]:
    captured: dict[str, FakeTicker] = {}

    def factory(api_key: str, access_token: str) -> FakeTicker:
        t = FakeTicker(api_key, access_token)
        captured["t"] = t
        return t

    return KiteTickerFeed("k", "a", ticker_factory=factory), captured


class _Strategy:
    def __init__(self) -> None:
        self.tick_batches: list[list[dict[str, Any]]] = []
        self.order_events: list[Any] = []

    def on_tick(self, ticks: list[dict[str, Any]]) -> None:
        self.tick_batches.append(ticks)

    def on_order(self, order: Any) -> None:
        self.order_events.append(order)


# ---- lifecycle --------------------------------------------------------


class TestLifecycle:
    async def test_start_connects_broker_and_subscribes(
        self, broker: KiteBroker, feed: tuple[KiteTickerFeed, dict[str, FakeTicker]]
    ) -> None:
        strategy = _Strategy()
        kfeed, captured = feed
        pipeline = DeploymentPipeline(
            broker=broker,
            feed=kfeed,
            strategy=strategy,
            instrument_tokens=[1, 2, 3],
        )
        assert pipeline.state.started is False

        await pipeline.start()
        assert pipeline.state.started is True
        assert pipeline.state.subscribed_tokens == [1, 2, 3]

        t = captured["t"]
        assert t.subscribed == [[1, 2, 3]]
        assert t.modes == [("quote", [1, 2, 3])]
        assert await broker.is_connected_async() is True

    async def test_start_is_idempotent(
        self, broker: KiteBroker, feed: tuple[KiteTickerFeed, dict[str, FakeTicker]]
    ) -> None:
        kfeed, captured = feed
        pipeline = DeploymentPipeline(
            broker=broker, feed=kfeed, strategy=_Strategy(), instrument_tokens=[7]
        )
        await pipeline.start()
        await pipeline.start()
        # Only one subscribe burst recorded.
        assert captured["t"].subscribed == [[7]]

    async def test_stop_closes_feed_and_disconnects_broker(
        self, broker: KiteBroker, feed: tuple[KiteTickerFeed, dict[str, FakeTicker]]
    ) -> None:
        kfeed, captured = feed
        pipeline = DeploymentPipeline(
            broker=broker, feed=kfeed, strategy=_Strategy(), instrument_tokens=[7]
        )
        await pipeline.start()
        await pipeline.stop()
        assert captured["t"].closed is True
        assert await broker.is_connected_async() is False
        assert pipeline.state.started is False

    async def test_stop_before_start_is_noop(
        self, broker: KiteBroker, feed: tuple[KiteTickerFeed, dict[str, FakeTicker]]
    ) -> None:
        kfeed, _ = feed
        pipeline = DeploymentPipeline(
            broker=broker, feed=kfeed, strategy=_Strategy(), instrument_tokens=[]
        )
        await pipeline.stop()
        # No exception -> pass.


# ---- strategy wiring ---------------------------------------------------


class TestStrategyWiring:
    async def test_on_tick_forwarded_to_strategy(
        self, broker: KiteBroker, feed: tuple[KiteTickerFeed, dict[str, FakeTicker]]
    ) -> None:
        strategy = _Strategy()
        kfeed, captured = feed
        pipeline = DeploymentPipeline(
            broker=broker, feed=kfeed, strategy=strategy, instrument_tokens=[1]
        )
        await pipeline.start()
        captured["t"].emit_ticks([{"instrument_token": 1, "last_price": 100}])
        assert len(strategy.tick_batches) == 1
        assert strategy.tick_batches[0][0]["last_price"] == 100

    async def test_on_order_registered_when_postbacks_provided(
        self, broker: KiteBroker, feed: tuple[KiteTickerFeed, dict[str, FakeTicker]]
    ) -> None:
        strategy = _Strategy()
        kfeed, _ = feed
        postbacks = PostbackHandler(api_secret="sekret", verify=False)
        pipeline = DeploymentPipeline(
            broker=broker,
            feed=kfeed,
            strategy=strategy,
            instrument_tokens=[1],
            postbacks=postbacks,
        )
        await pipeline.start()

        import json

        postbacks.handle(
            json.dumps(
                {
                    "order_id": "42",
                    "status": "COMPLETE",
                    "exchange": "NSE",
                    "tradingsymbol": "RELIANCE",
                    "transaction_type": "BUY",
                    "order_type": "MARKET",
                    "quantity": 1,
                    "filled_quantity": 1,
                    "average_price": 2500.0,
                }
            ).encode()
        )
        assert len(strategy.order_events) == 1
        assert strategy.order_events[0].order_id == "42"

    async def test_strategy_without_callbacks_is_tolerated(
        self, broker: KiteBroker, feed: tuple[KiteTickerFeed, dict[str, FakeTicker]]
    ) -> None:
        """A bare-bones object with no handlers should not crash start()."""
        kfeed, captured = feed
        pipeline = DeploymentPipeline(
            broker=broker, feed=kfeed, strategy=object(), instrument_tokens=[1]
        )
        await pipeline.start()
        # Emitting ticks must not crash either.
        captured["t"].emit_ticks([{"instrument_token": 1}])

"""Tests for :class:`ml4t.india.live.kite_ticker_feed.KiteTickerFeed`.

A tiny :class:`FakeTicker` stands in for :class:`kiteconnect.KiteTicker`
so no network is touched and tick delivery can be triggered
deterministically.
"""

from __future__ import annotations

from typing import Any

import pytest

from ml4t.india.core.exceptions import InvalidInputError
from ml4t.india.live.feed_base import IndianTickerFeedBase
from ml4t.india.live.kite_ticker_feed import KiteTickerFeed, validate_mode


class FakeTicker:
    """In-memory stand-in for :class:`kiteconnect.KiteTicker`.

    Matches the surface ``KiteTickerFeed`` depends on and records every
    call. Does NOT spawn a thread on :meth:`connect`; tests trigger
    handlers manually via :meth:`emit_*`.
    """

    on_ticks: Any = None
    on_connect: Any = None
    on_close: Any = None
    on_error: Any = None

    def __init__(self, api_key: str, access_token: str) -> None:
        self.api_key = api_key
        self.access_token = access_token
        self.subscribed: list[list[int]] = []
        self.unsubscribed: list[list[int]] = []
        self.modes: list[tuple[str, list[int]]] = []
        self.connected: bool = False
        self.closed: bool = False

    def connect(self, threaded: bool = True) -> None:
        self.connected = True

    def close(self, code: int | None = None, reason: str | None = None) -> None:
        self.closed = True
        self.connected = False

    def subscribe(self, instrument_tokens: list[int]) -> None:
        self.subscribed.append(list(instrument_tokens))

    def unsubscribe(self, instrument_tokens: list[int]) -> None:
        self.unsubscribed.append(list(instrument_tokens))

    def set_mode(self, mode: str, instrument_tokens: list[int]) -> None:
        self.modes.append((mode, list(instrument_tokens)))

    def is_connected(self) -> bool:
        return self.connected

    # ---- test helpers ----

    def emit_ticks(self, ticks: list[dict[str, Any]]) -> None:
        if self.on_ticks is not None:
            self.on_ticks(self, ticks)

    def emit_connect(self, response: Any = None) -> None:
        if self.on_connect is not None:
            self.on_connect(self, response)

    def emit_close(self, code: int, reason: str) -> None:
        if self.on_close is not None:
            self.on_close(self, code, reason)

    def emit_error(self, code: int, reason: str) -> None:
        if self.on_error is not None:
            self.on_error(self, code, reason)


# ---- inheritance -------------------------------------------------------


class TestInheritance:
    def test_is_indian_ticker_feed_base(self) -> None:
        feed = KiteTickerFeed("k", "a", ticker_factory=lambda *_: FakeTicker("", ""))
        assert isinstance(feed, IndianTickerFeedBase)

    def test_no_abstract_methods_left(self) -> None:
        assert not getattr(KiteTickerFeed, "__abstractmethods__", set())


# ---- mode validator ----------------------------------------------------


class TestValidateMode:
    @pytest.mark.parametrize("mode", ["ltp", "quote", "full"])
    def test_valid(self, mode: str) -> None:
        assert validate_mode(mode) == mode

    def test_invalid(self) -> None:
        with pytest.raises(InvalidInputError, match="mode must be"):
            validate_mode("tick")


# ---- subscription before start ----------------------------------------


class TestSubscribeBeforeStart:
    def test_pending_subscription_held_until_connect(self) -> None:
        def factory(*_: Any) -> FakeTicker:
            return FakeTicker("k", "a")

        feed = KiteTickerFeed("k", "a", ticker_factory=factory)
        feed.subscribe([1, 2, 3])
        # No ticker yet, nothing was pushed
        assert feed.subscriptions == {1: "quote", 2: "quote", 3: "quote"}


# ---- subscription after start -----------------------------------------


class TestSubscribeAfterStart:
    async def test_live_subscribe_propagates(self) -> None:
        captured: dict[str, FakeTicker] = {}

        def factory(api_key: str, access_token: str) -> FakeTicker:
            t = FakeTicker(api_key, access_token)
            captured["t"] = t
            return t

        feed = KiteTickerFeed("k", "a", ticker_factory=factory)
        await feed.start()
        feed.subscribe([10, 20], mode="full")

        t = captured["t"]
        assert t.subscribed == [[10, 20]]
        assert t.modes == [("full", [10, 20])]

    async def test_unsubscribe_live(self) -> None:
        captured: dict[str, FakeTicker] = {}

        def factory(api_key: str, access_token: str) -> FakeTicker:
            t = FakeTicker(api_key, access_token)
            captured["t"] = t
            return t

        feed = KiteTickerFeed("k", "a", ticker_factory=factory)
        feed.subscribe([7, 8])
        await feed.start()
        feed.unsubscribe([7])

        t = captured["t"]
        assert t.unsubscribed == [[7]]
        assert feed.subscriptions == {8: "quote"}


# ---- tick fan-out -----------------------------------------------------


class TestTickFanOut:
    async def test_all_handlers_called(self) -> None:
        captured: dict[str, FakeTicker] = {}

        def factory(*_: Any) -> FakeTicker:
            t = FakeTicker("k", "a")
            captured["t"] = t
            return t

        feed = KiteTickerFeed("k", "a", ticker_factory=factory)
        seen_a: list[list[dict[str, Any]]] = []
        seen_b: list[list[dict[str, Any]]] = []
        feed.on_ticks(seen_a.append)
        feed.on_ticks(seen_b.append)

        await feed.start()
        captured["t"].emit_ticks([{"instrument_token": 1, "last_price": 100.0}])

        assert len(seen_a) == 1
        assert len(seen_b) == 1
        assert seen_a[0][0]["last_price"] == 100.0

    async def test_bad_handler_isolated(self) -> None:
        captured: dict[str, FakeTicker] = {}

        def factory(*_: Any) -> FakeTicker:
            t = FakeTicker("k", "a")
            captured["t"] = t
            return t

        feed = KiteTickerFeed("k", "a", ticker_factory=factory)

        def bad(_: list[dict[str, Any]]) -> None:
            raise RuntimeError("handler boom")

        good_seen: list[int] = []

        def good(ticks: list[dict[str, Any]]) -> None:
            good_seen.append(len(ticks))

        feed.on_ticks(bad)
        feed.on_ticks(good)

        await feed.start()
        captured["t"].emit_ticks([{"x": 1}])

        assert good_seen == [1]


# ---- reconnect replay --------------------------------------------------


class TestReconnectReplay:
    async def test_on_connect_replays_subscription(self) -> None:
        captured: dict[str, FakeTicker] = {}

        def factory(*_: Any) -> FakeTicker:
            t = FakeTicker("k", "a")
            captured["t"] = t
            return t

        feed = KiteTickerFeed("k", "a", ticker_factory=factory)
        feed.subscribe([1, 2], mode="ltp")
        feed.subscribe([3], mode="full")
        await feed.start()
        t = captured["t"]

        # Clear any subscribe calls from the direct live-push.
        t.subscribed.clear()
        t.modes.clear()

        # Fire reconnect; on_connect handler replays subscription map.
        t.emit_connect()

        # Two modes in play -> two subscribe + set_mode batches.
        assert sorted(t.subscribed, key=sorted) == [[1, 2], [3]]
        assert {m for m, _ in t.modes} == {"ltp", "full"}


# ---- stop --------------------------------------------------------------


class TestStop:
    async def test_stop_closes_ticker(self) -> None:
        captured: dict[str, FakeTicker] = {}

        def factory(*_: Any) -> FakeTicker:
            t = FakeTicker("k", "a")
            captured["t"] = t
            return t

        feed = KiteTickerFeed("k", "a", ticker_factory=factory)
        await feed.start()
        feed.stop()
        assert captured["t"].closed is True

    def test_stop_idempotent_before_start(self) -> None:
        feed = KiteTickerFeed("k", "a", ticker_factory=lambda *_: FakeTicker("", ""))
        feed.stop()  # should not raise
        feed.stop()  # idempotent


# ---- close / error handlers -------------------------------------------


class TestCloseAndError:
    async def test_on_close_fans_out(self) -> None:
        captured: dict[str, FakeTicker] = {}

        def factory(*_: Any) -> FakeTicker:
            t = FakeTicker("k", "a")
            captured["t"] = t
            return t

        feed = KiteTickerFeed("k", "a", ticker_factory=factory)
        seen: list[tuple[int, str]] = []
        feed.on_close(lambda code, reason: seen.append((code, reason)))
        await feed.start()
        captured["t"].emit_close(1006, "network down")
        assert seen == [(1006, "network down")]

    async def test_on_error_fans_out(self) -> None:
        captured: dict[str, FakeTicker] = {}

        def factory(*_: Any) -> FakeTicker:
            t = FakeTicker("k", "a")
            captured["t"] = t
            return t

        feed = KiteTickerFeed("k", "a", ticker_factory=factory)
        seen: list[tuple[int, str]] = []
        feed.on_error(lambda code, reason: seen.append((code, reason)))
        await feed.start()
        captured["t"].emit_error(500, "boom")
        assert seen == [(500, "boom")]

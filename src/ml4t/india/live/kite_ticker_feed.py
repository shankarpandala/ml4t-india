"""Concrete Zerodha Kite WebSocket feed implementing :class:`IndianTickerFeedBase`.

:class:`KiteTickerFeed` wraps :class:`kiteconnect.KiteTicker` so that
the upstream ``DataFeedProtocol`` (``start`` async, ``stop`` sync) is
satisfied, and downstream ml4t-india code (risk, OMS, diagnostics)
can register tick handlers without importing ``kiteconnect`` directly.

Design
------

* ``KiteTicker`` runs a Twisted reactor in its own thread once
  :meth:`connect` is called. That is already async-friendly: we start
  it and return. Stopping hands off via :meth:`KiteTicker.close`,
  which finalises the reactor.

* Tests inject a ``ticker_factory`` callable so we never touch the
  network. The factory returns any object that implements the small
  :class:`_TickerSurface` protocol (``subscribe`` / ``unsubscribe`` /
  ``set_mode`` / ``close`` / event-handler slots). A tiny fake lives
  in :mod:`tests/unit/test_kite_ticker_feed.py`.

* Ticks fan out to *all* registered ``on_ticks`` callbacks; failing
  callbacks do not break the others (we log + continue). Callbacks
  receive Kite's native dict shape so concrete strategies can pick
  the fields they need without paying for an eager translation.

* Subscription state is owned by this class, not by ``KiteTicker``.
  On reconnect we re-send the full subscription + mode, so a dropped
  socket does not silently cost ticks.

* Modes map 1-to-1 to Kite wire values (``"ltp"`` / ``"quote"`` /
  ``"full"``). Strings are used rather than an enum because
  ``kiteconnect`` exposes them as class constants, and our
  :mod:`~ml4t.india.core.constants` enums pin Kite wire strings
  directly when it matters.
"""

from __future__ import annotations

import contextlib
import threading
from collections.abc import Callable, Iterable
from typing import Any, Literal, Protocol, runtime_checkable

from ml4t.india.core.exceptions import InvalidInputError
from ml4t.india.live.feed_base import IndianTickerFeedBase

TickMode = Literal["ltp", "quote", "full"]
#: Handler signatures. Using ``Any`` for tick payload keeps us decoupled
#: from Kite's dict schema evolving -- callers who want static typing
#: can TypedDict it themselves.
TicksHandler = Callable[[list[dict[str, Any]]], None]
ConnectHandler = Callable[[], None]
CloseHandler = Callable[[int, str], None]
ErrorHandler = Callable[[int, str], None]


@runtime_checkable
class _TickerSurface(Protocol):
    """Structural protocol the injected ticker must satisfy.

    Matches the subset of :class:`kiteconnect.KiteTicker` we depend on.
    """

    on_ticks: Any
    on_connect: Any
    on_close: Any
    on_error: Any

    def connect(self, threaded: bool = ...) -> None: ...
    def close(self, code: int | None = ..., reason: str | None = ...) -> None: ...
    def subscribe(self, instrument_tokens: list[int]) -> None: ...
    def unsubscribe(self, instrument_tokens: list[int]) -> None: ...
    def set_mode(self, mode: str, instrument_tokens: list[int]) -> None: ...
    def is_connected(self) -> bool: ...


#: Factory signature for injecting a ticker. Defaults to the real
#: :class:`kiteconnect.KiteTicker`; tests override with a fake.
TickerFactory = Callable[[str, str], _TickerSurface]


def _default_ticker_factory(api_key: str, access_token: str) -> _TickerSurface:
    """Import :class:`kiteconnect.KiteTicker` lazily.

    Kept local to this module so a caller that never touches the
    default path doesn't need ``kiteconnect`` importable (useful in
    pure-unit-test environments).
    """
    from kiteconnect import KiteTicker

    return KiteTicker(api_key=api_key, access_token=access_token)


class KiteTickerFeed(IndianTickerFeedBase):
    """Zerodha Kite binary-WebSocket feed.

    Parameters
    ----------
    api_key:
        Kite API key -- forwarded to the ticker factory.
    access_token:
        Valid access token from :class:`~ml4t.india.kite.auth` flow.
    ticker_factory:
        Callable producing a :class:`_TickerSurface` instance. Defaults
        to :class:`kiteconnect.KiteTicker`; override in tests.
    default_mode:
        Subscription mode to apply when :meth:`subscribe` is called
        without an explicit ``mode``. One of ``"ltp"`` / ``"quote"`` /
        ``"full"``.
    """

    def __init__(
        self,
        api_key: str,
        access_token: str,
        ticker_factory: TickerFactory | None = None,
        default_mode: TickMode = "quote",
    ) -> None:
        self._api_key = api_key
        self._access_token = access_token
        self._factory: TickerFactory = ticker_factory or _default_ticker_factory
        self._default_mode: TickMode = default_mode

        # Subscription state -- owned HERE, not delegated to KiteTicker,
        # so we can replay it on reconnect. Keyed by instrument_token
        # so subscription order is preserved across mode changes.
        self._subscriptions: dict[int, TickMode] = {}

        # Lazily constructed ticker; None until :meth:`start`.
        self._ticker: _TickerSurface | None = None
        self._running: bool = False

        # Lock guards self._subscriptions + self._running against
        # callbacks arriving on the Twisted thread.
        self._lock = threading.RLock()

        # Registered handlers. A list (not a single slot) so multiple
        # consumers -- strategy, risk, diagnostics -- can all watch
        # the same feed without re-subscribing.
        self._on_ticks: list[TicksHandler] = []
        self._on_connect: list[ConnectHandler] = []
        self._on_close: list[CloseHandler] = []
        self._on_error: list[ErrorHandler] = []

    # ---- handler registration --------------------------------------

    def on_ticks(self, handler: TicksHandler) -> None:
        """Register a callback to run on every batch of ticks.

        Multiple handlers are supported; they run in registration order
        and a handler raising does NOT stop other handlers.
        """
        self._on_ticks.append(handler)

    def on_connect(self, handler: ConnectHandler) -> None:
        self._on_connect.append(handler)

    def on_close(self, handler: CloseHandler) -> None:
        self._on_close.append(handler)

    def on_error(self, handler: ErrorHandler) -> None:
        self._on_error.append(handler)

    # ---- subscription management -----------------------------------

    def subscribe(
        self,
        instrument_tokens: Iterable[int],
        mode: TickMode | None = None,
    ) -> None:
        """Add ``instrument_tokens`` to the subscription.

        Safe to call before OR after :meth:`start`. If called before,
        the tokens are remembered and sent on connect. If after, they
        are pushed to the live ticker immediately.
        """
        tokens = list(instrument_tokens)
        if not tokens:
            return
        chosen_mode: TickMode = mode or self._default_mode
        with self._lock:
            for t in tokens:
                self._subscriptions[int(t)] = chosen_mode
            live_ticker = self._ticker if self._running else None
        if live_ticker is not None:
            live_ticker.subscribe(tokens)
            live_ticker.set_mode(chosen_mode, tokens)

    def unsubscribe(self, instrument_tokens: Iterable[int]) -> None:
        """Remove ``instrument_tokens`` from the subscription."""
        tokens = list(instrument_tokens)
        if not tokens:
            return
        with self._lock:
            for t in tokens:
                self._subscriptions.pop(int(t), None)
            live_ticker = self._ticker if self._running else None
        if live_ticker is not None:
            live_ticker.unsubscribe(tokens)

    @property
    def subscriptions(self) -> dict[int, TickMode]:
        """Snapshot of the current subscription map."""
        with self._lock:
            return dict(self._subscriptions)

    # ---- lifecycle --------------------------------------------------

    async def start(self) -> None:
        """Connect the websocket and push the current subscription.

        ``KiteTicker.connect(threaded=True)`` launches the Twisted
        reactor on its own thread and returns. We therefore do not
        need ``run_in_executor``; the sync call completes quickly.
        Idempotent.
        """
        with self._lock:
            if self._running:
                return
            self._ticker = self._factory(self._api_key, self._access_token)
            self._wire_handlers(self._ticker)
            self._running = True

        # Outside the lock: connect() may start a thread that calls back
        # into our handlers, which also grab the lock.
        self._ticker.connect(threaded=True)

    def stop(self) -> None:
        """Close the websocket. Idempotent; synchronous by protocol."""
        with self._lock:
            ticker, self._ticker = self._ticker, None
            self._running = False
        if ticker is not None:
            ticker.close()

    # ---- wiring + callback translation -----------------------------

    def _wire_handlers(self, ticker: _TickerSurface) -> None:
        """Attach Kite-style slot callbacks that fan out to our handler lists.

        Kite passes ``ws`` as the first positional argument to every
        callback (so handlers can call :meth:`ws.stop` etc.). We
        accept + ignore it to keep our public handler signatures
        ``ws``-free.
        """

        def _on_ticks(_ws: Any, ticks: list[dict[str, Any]]) -> None:
            # Isolating a bad handler -- logging happens at the caller's
            # chosen layer (structlog), we deliberately avoid coupling
            # to it here so a silent handler does not break siblings.
            for handler in list(self._on_ticks):
                with contextlib.suppress(Exception):
                    handler(ticks)

        def _on_connect(_ws: Any, _response: Any) -> None:
            # On every (re)connect, replay the full subscription so a
            # dropped socket does not cost ticks.
            with self._lock:
                tokens_by_mode: dict[TickMode, list[int]] = {}
                for token, mode in self._subscriptions.items():
                    tokens_by_mode.setdefault(mode, []).append(token)
                live_ticker = self._ticker
            if live_ticker is not None:
                for mode, tokens in tokens_by_mode.items():
                    live_ticker.subscribe(tokens)
                    live_ticker.set_mode(mode, tokens)
            for handler in list(self._on_connect):
                with contextlib.suppress(Exception):
                    handler()

        def _on_close(_ws: Any, code: int, reason: str) -> None:
            for handler in list(self._on_close):
                with contextlib.suppress(Exception):
                    handler(code, reason)

        def _on_error(_ws: Any, code: int, reason: str) -> None:
            for handler in list(self._on_error):
                with contextlib.suppress(Exception):
                    handler(code, reason)

        ticker.on_ticks = _on_ticks
        ticker.on_connect = _on_connect
        ticker.on_close = _on_close
        ticker.on_error = _on_error


def validate_mode(mode: str) -> TickMode:
    """Raise :class:`InvalidInputError` if ``mode`` isn't a Kite tick mode."""
    if mode not in ("ltp", "quote", "full"):
        raise InvalidInputError(f"mode must be 'ltp' | 'quote' | 'full' (got {mode!r})")
    return mode  # type: ignore[return-value]


__all__ = ["KiteTickerFeed", "TickMode", "validate_mode"]

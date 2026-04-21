"""Smoke + contract tests for :mod:`ml4t.india.live`.

Same pattern as :mod:`tests.unit.test_indian_ohlcv_provider`: assertions
stay inside our own class body (``vars(cls)`` / ``issubclass``) so they do
not depend on brittle upstream detail. Full behavioural tests will land
alongside the first concrete :class:`KiteBroker` / :class:`KiteTickerFeed`
in Phase 4.
"""

from __future__ import annotations

import inspect

import pytest

from ml4t.india.live import IndianBrokerBase, IndianTickerFeedBase

pytestmark = pytest.mark.contract


class TestIndianBrokerBase:
    def test_is_abstract(self) -> None:
        """Base cannot be instantiated directly; subclasses must implement."""
        assert inspect.isabstract(IndianBrokerBase)
        with pytest.raises(TypeError):
            IndianBrokerBase()  # type: ignore[abstract]

    def test_declares_full_async_broker_surface(self) -> None:
        """Every method required by ml4t.live.AsyncBrokerProtocol is declared."""
        expected = {
            "connect",
            "disconnect",
            "is_connected_async",
            "get_account_value_async",
            "get_cash_async",
            "get_position_async",
            "get_positions_async",
            "close_position_async",
            "submit_order_async",
            "cancel_order_async",
            "get_pending_orders_async",
        }
        missing = expected - set(vars(IndianBrokerBase))
        assert not missing, f"IndianBrokerBase is missing: {missing}"

    def test_every_declared_method_is_abstract(self) -> None:
        """None of our declared methods have sneaked in with a default impl."""
        abstract = IndianBrokerBase.__abstractmethods__
        declared = {
            n for n, v in vars(IndianBrokerBase).items()
            if callable(v) and not n.startswith("_")
        }
        assert declared <= abstract, (
            f"Concrete method(s) where abstractmethod expected: {declared - abstract}"
        )


class TestIndianTickerFeedBase:
    def test_is_abstract(self) -> None:
        assert inspect.isabstract(IndianTickerFeedBase)
        with pytest.raises(TypeError):
            IndianTickerFeedBase()  # type: ignore[abstract]

    def test_start_is_async_stop_is_sync(self) -> None:
        """Mirror upstream DataFeedProtocol exactly: start async, stop sync."""
        assert inspect.iscoroutinefunction(IndianTickerFeedBase.start)
        assert not inspect.iscoroutinefunction(IndianTickerFeedBase.stop)

    def test_abstract_method_set(self) -> None:
        assert IndianTickerFeedBase.__abstractmethods__ == frozenset({"start", "stop"})

"""Upstream-API drift guard for ml4t-india.

This module freezes the specific parts of the upstream ml4t-* API that
our code imports today. If upstream renames a method, drops an attribute,
or changes a signature we depend on, one of these assertions will fail
with a clear message pointing at the drift -- long before the adapter
code using that API starts misbehaving in subtle ways at runtime.

Scope
-----

Assertions here cover ONLY the surface area ml4t-india actually touches:

* :class:`ml4t.data.providers.base.BaseProvider` -- extended by
  :class:`ml4t.india.data.IndianOHLCVProvider`.
* :class:`ml4t.live.protocols.AsyncBrokerProtocol` -- satisfied by
  :class:`ml4t.india.live.IndianBrokerBase`.
* :class:`ml4t.live.protocols.DataFeedProtocol` -- satisfied by
  :class:`ml4t.india.live.IndianTickerFeedBase`.
* :mod:`ml4t.backtest.types` -- Order / OrderSide / OrderType /
  Position types used in our broker signatures.

As new phases touch new upstream symbols the test suite is extended
here; that is the signal to CI that our drift-guard coverage is in
sync with our dependency surface.

How to interpret a failure
--------------------------

* ``AttributeError`` on one of the imports: upstream renamed or removed
  a module / class. Decide whether to follow the rename or stay pinned.
* ``AssertionError`` on a signature / attribute membership: upstream
  changed the shape of something we depend on. Inspect the failing line
  and update our adapter code OR narrow the upstream pin if the change
  is breaking.

The test is kept in a dedicated ``tests/contracts`` tree so a future
``pytest -m contract`` CI lane can run just this file on a weekly
cron against the very latest upstream wheels (Phase 0.8).
"""

from __future__ import annotations

import inspect

import pytest

pytestmark = pytest.mark.contract


class TestMl4tDataBaseProvider:
    """Pin the ``ml4t.data.providers.base.BaseProvider`` surface we use."""

    def test_importable(self) -> None:
        from ml4t.data.providers.base import BaseProvider  # noqa: F401

    def test_is_abstract(self) -> None:
        from ml4t.data.providers.base import BaseProvider

        assert inspect.isabstract(BaseProvider), (
            "BaseProvider must remain abstract; our IndianOHLCVProvider "
            "extends it and relies on Python's ABC enforcement for the "
            "`name` contract."
        )

    def test_name_property_is_abstract(self) -> None:
        from ml4t.data.providers.base import BaseProvider

        assert "name" in BaseProvider.__abstractmethods__, (
            "BaseProvider.name must stay @abstractmethod; concrete "
            "Indian providers (KiteProvider etc.) rely on ABCMeta to "
            "force them to declare a name at class-definition time."
        )

    def test_fetch_ohlcv_signature_unchanged(self) -> None:
        from ml4t.data.providers.base import BaseProvider

        sig = inspect.signature(BaseProvider.fetch_ohlcv)
        assert list(sig.parameters) == ["self", "symbol", "start", "end", "frequency"], (
            "BaseProvider.fetch_ohlcv parameter list changed; update "
            "ml4t.india.data.base docs and any code that delegates "
            "through to it."
        )


class TestMl4tLiveProtocols:
    """Pin the ``ml4t.live.protocols`` protocol shapes we satisfy."""

    def test_async_broker_protocol_methods_present(self) -> None:
        from ml4t.live.protocols import AsyncBrokerProtocol

        required = {
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
        missing = required - set(dir(AsyncBrokerProtocol))
        assert not missing, (
            f"AsyncBrokerProtocol dropped method(s): {missing}. "
            "IndianBrokerBase declared them as abstract expecting them to "
            "exist; follow the rename or update our base."
        )

    def test_submit_order_async_signature(self) -> None:
        from ml4t.live.protocols import AsyncBrokerProtocol

        sig = inspect.signature(AsyncBrokerProtocol.submit_order_async)
        params = list(sig.parameters)
        # self + 6 documented params + **kwargs.
        assert params[:7] == [
            "self",
            "asset",
            "quantity",
            "side",
            "order_type",
            "limit_price",
            "stop_price",
        ], (
            "AsyncBrokerProtocol.submit_order_async parameter order "
            f"drifted: got {params}. IndianBrokerBase.submit_order_async "
            "mirrors this order and will confuse type checkers if it "
            "diverges."
        )
        # Kwargs tail is what keeps broker-specific fields extensible.
        assert any(
            p.kind is inspect.Parameter.VAR_KEYWORD for p in sig.parameters.values()
        ), "submit_order_async must still accept **kwargs for broker-specific fields."

    def test_data_feed_protocol_start_async_stop_sync(self) -> None:
        from ml4t.live.protocols import DataFeedProtocol

        assert inspect.iscoroutinefunction(DataFeedProtocol.start), (
            "DataFeedProtocol.start flipped away from async; "
            "IndianTickerFeedBase.start mirrors its coroutine-ness."
        )
        assert not inspect.iscoroutinefunction(DataFeedProtocol.stop), (
            "DataFeedProtocol.stop flipped to async; "
            "IndianTickerFeedBase.stop mirrors its sync-ness."
        )


class TestMl4tBacktestTypes:
    """Pin the ``ml4t.backtest.types`` names our broker signatures use."""

    @pytest.mark.parametrize("name", ["Order", "OrderSide", "OrderType", "Position"])
    def test_importable(self, name: str) -> None:
        import importlib

        mod = importlib.import_module("ml4t.backtest.types")
        assert hasattr(mod, name), (
            f"ml4t.backtest.types dropped '{name}'. IndianBrokerBase "
            "annotations and submit_order_async default arg "
            "OrderType.MARKET depend on these names."
        )

    def test_order_type_has_market_member(self) -> None:
        from ml4t.backtest.types import OrderType

        assert hasattr(OrderType, "MARKET"), (
            "OrderType.MARKET is the default for submit_order_async; "
            "removing it would break every subclass of IndianBrokerBase."
        )

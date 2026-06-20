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
            "replace_order_async",
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

    def test_order_type_moc_member_is_handled_when_present(self) -> None:
        """``OrderType.MOC`` was added upstream 2026-04-27.

        We do NOT require it to exist (older backtest versions can still
        be installed), but if it does we must handle it in every broker
        translator -- otherwise unit tests for MOC will fail at runtime.
        """
        from ml4t.backtest.types import OrderType

        moc = getattr(OrderType, "MOC", None)
        if moc is None:
            pytest.skip("upstream OrderType has no MOC member yet")

        # Each broker's _<broker>_order_type must accept MOC.
        from ml4t.india.live.angelone_broker import _angel_order_type
        from ml4t.india.live.fivepaisa_broker import _fivepaisa_order_type
        from ml4t.india.live.kite_broker import _ml4t_to_kite_order_type
        from ml4t.india.live.upstox_broker import _upstox_order_type

        # Every translator should not raise on MOC (the wire string each
        # broker chooses is broker-specific; we only check that the
        # mapping exists at all).
        _ml4t_to_kite_order_type(moc)
        _upstox_order_type(moc)
        _angel_order_type(moc)
        _fivepaisa_order_type(moc)


class TestKiteconnectAutoSlice:
    """``KiteConnect.place_autoslice_order`` was added in kiteconnect 5.2.

    Our :class:`~ml4t.india.kite.client.KiteClient` facade wraps it, so the
    method must remain present upstream. If kiteconnect drops or renames it,
    we want this assertion to fail loudly before the broker layer does.
    """

    def test_place_autoslice_order_present_on_sdk(self) -> None:
        from kiteconnect import KiteConnect

        assert hasattr(KiteConnect, "place_autoslice_order"), (
            "kiteconnect.KiteConnect dropped place_autoslice_order; "
            "KiteClient.place_autoslice_order and "
            "KiteBroker.submit_order_async(auto_slice=True) depend on it."
        )

    def test_kite_client_facade_wraps_it(self) -> None:
        from ml4t.india.kite.client import AsyncKiteClient, KiteClient

        assert hasattr(KiteClient, "place_autoslice_order"), (
            "KiteClient facade lost its place_autoslice_order wrapper."
        )
        assert hasattr(AsyncKiteClient, "place_autoslice_order"), (
            "AsyncKiteClient facade lost its place_autoslice_order wrapper."
        )


class TestMl4tAgentResearchLoop:
    """Pin the ``ml4t.agent`` surface :class:`IndiaResearchAgent` wraps.

    ml4t-agent is the opt-in ``agent`` extra (alpha); when it is not
    installed these tests skip rather than fail, mirroring how the wrapper
    degrades. When it *is* installed they freeze the exact constructor /
    run / LineState shape our wrapper threads India defaults through.
    """

    def test_research_review_agent_init_signature(self) -> None:
        agent = pytest.importorskip("ml4t.agent")

        sig = inspect.signature(agent.ResearchReviewAgent.__init__)
        params = list(sig.parameters)
        assert params[:3] == ["self", "llm", "line_state"], (
            "ResearchReviewAgent.__init__ leading params drifted: got "
            f"{params}. IndiaResearchAgent calls ResearchReviewAgent("
            "llm=..., line_state=...) and forwards the rest as **overrides."
        )
        for name in ("templates", "max_steps"):
            assert name in sig.parameters, (
                f"ResearchReviewAgent.__init__ dropped keyword {name!r}; "
                "IndiaResearchAgent forwards it via **agent_overrides."
            )

    def test_research_review_agent_run_signature(self) -> None:
        agent = pytest.importorskip("ml4t.agent")

        sig = inspect.signature(agent.ResearchReviewAgent.run)
        assert list(sig.parameters) == ["self", "run_dir"], (
            "ResearchReviewAgent.run parameter list changed; "
            "IndiaResearchAgent.run delegates straight through to it."
        )

    def test_line_state_fields(self) -> None:
        pytest.importorskip("ml4t.agent")
        import dataclasses

        from ml4t.agent.schemas.workflow import LineState

        fields = {f.name for f in dataclasses.fields(LineState)}
        required = {"line_id", "iteration_index", "max_iterations", "base_alpha"}
        missing = required - fields
        assert not missing, (
            f"ml4t.agent LineState dropped field(s): {missing}. "
            "IndiaResearchAgent builds LineState(line_id=..., "
            "iteration_index=...) and relies on the documented defaults."
        )

    def test_mock_llm_client_present(self) -> None:
        agent = pytest.importorskip("ml4t.agent")

        assert hasattr(agent, "MockLLMClient"), (
            "ml4t.agent dropped MockLLMClient; IndiaResearchAgent's keyless "
            "default LLM depends on it."
        )

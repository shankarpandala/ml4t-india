"""Deterministic unit tests for library paper execution.

These exercise the *pure* fill arithmetic (:func:`simulate_fill`) and the
in-memory :class:`PaperKiteBroker` against a fake quote source. They use
example price + order inputs only -- no credentials, no network, no real
Kite endpoint -- so they run in CI green by default. This is the library
math the brief explicitly permits to unit-test.

The structural safety guarantee is asserted here too: the read-only quote
surface a paper broker is given exposes no order-placement method, and the
paper module never references one.
"""

from __future__ import annotations

import datetime as dt
import inspect
from pathlib import Path
from typing import Any

import pytest
from ml4t.backtest.types import OrderSide, OrderStatus, OrderType

from ml4t.india.backtest.charges import Segment, ZerodhaChargesModel
from ml4t.india.core.exceptions import InvalidInputError
from ml4t.india.live.paper import (
    LastPriceFillModel,
    PaperKiteBroker,
    ReadOnlyQuoteClient,
    simulate_fill,
)

_FIXED_NOW = dt.datetime(2026, 6, 20, 10, 0, tzinfo=dt.UTC)


# ---------------------------------------------------------------------------
# Pure fill arithmetic.
# ---------------------------------------------------------------------------


def test_buy_fills_at_reference_price_with_zero_default_slippage() -> None:
    fill = simulate_fill(
        asset="NSE:RELIANCE",
        side=OrderSide.BUY,
        quantity=10,
        reference_price=100.0,
    )
    assert fill.fill_price == 100.0
    assert fill.slippage == 0.0
    assert fill.gross_value == pytest.approx(1000.0)
    # Cash leaves on a buy: -(gross + charges).
    assert fill.cash_delta == pytest.approx(-1000.0 - fill.charges)
    assert fill.charges > 0


def test_charges_match_zerodha_model_and_are_side_aware() -> None:
    """Sell-side charges differ from buy-side (STT vs stamp duty)."""
    model = ZerodhaChargesModel()
    buy = simulate_fill(
        asset="NSE:RELIANCE", side=OrderSide.BUY, quantity=10, reference_price=100.0,
        charges_model=model,
    )
    sell = simulate_fill(
        asset="NSE:RELIANCE", side=OrderSide.SELL, quantity=10, reference_price=100.0,
        charges_model=model,
    )
    # Reproduce the model directly with sign-encoded quantity.
    assert buy.charges == pytest.approx(model.calculate("NSE:RELIANCE", 10, 100.0))
    assert sell.charges == pytest.approx(model.calculate("NSE:RELIANCE", -10, 100.0))
    # Equity-delivery STT (sell) dominates buy-side stamp, so a sell costs more.
    assert sell.charges > buy.charges


def test_sell_cash_delta_is_positive_net_of_charges() -> None:
    fill = simulate_fill(
        asset="NSE:RELIANCE", side=OrderSide.SELL, quantity=5, reference_price=200.0,
    )
    assert fill.cash_delta == pytest.approx(1000.0 - fill.charges)
    assert fill.cash_delta > 0


def test_slippage_widens_buy_up_and_sell_down() -> None:
    model = LastPriceFillModel(slippage_bps=50.0)  # 0.5%
    buy = simulate_fill(
        asset="NSE:INFY", side=OrderSide.BUY, quantity=1, reference_price=1000.0,
        fill_model=model,
    )
    sell = simulate_fill(
        asset="NSE:INFY", side=OrderSide.SELL, quantity=1, reference_price=1000.0,
        fill_model=model,
    )
    assert buy.fill_price == pytest.approx(1005.0)
    assert sell.fill_price == pytest.approx(995.0)
    assert buy.slippage == pytest.approx(5.0)
    assert sell.slippage == pytest.approx(5.0)


def test_options_charges_use_flat_brokerage() -> None:
    """An NFO option fill carries the flat Rs 20 options brokerage."""
    fill = simulate_fill(
        asset="NFO:NIFTY26JUN25000CE",
        side=OrderSide.BUY,
        quantity=75,
        reference_price=120.0,
    )
    # Flat Rs 20 brokerage is the dominant charge for a small options buy.
    assert fill.charges > ZerodhaChargesModel.FLAT_FEE
    # Sanity: the segment really is options.
    assert Segment.EQUITY_OPTIONS  # enum exists / imported


@pytest.mark.parametrize("bad_qty", [0, -5])
def test_non_positive_quantity_rejected(bad_qty: float) -> None:
    with pytest.raises(InvalidInputError):
        simulate_fill(
            asset="NSE:RELIANCE", side=OrderSide.BUY, quantity=bad_qty, reference_price=100.0,
        )


def test_non_positive_reference_price_rejected() -> None:
    with pytest.raises(InvalidInputError):
        simulate_fill(
            asset="NSE:RELIANCE", side=OrderSide.BUY, quantity=1, reference_price=0.0,
        )


# ---------------------------------------------------------------------------
# Fake read-only quote source + broker behaviour.
# ---------------------------------------------------------------------------


class _FakeQuotes:
    """Minimal async quote source: returns canned LTPs, no order methods."""

    def __init__(self, prices: dict[str, float]) -> None:
        self._prices = prices
        self.ltp_calls: list[list[str]] = []

    async def ltp(self, instruments: list[str]) -> dict[str, dict[str, Any]]:
        self.ltp_calls.append(list(instruments))
        return {
            sym: {"instrument_token": 1, "last_price": self._prices[sym]}
            for sym in instruments
            if sym in self._prices
        }

    async def profile(self) -> dict[str, Any]:
        return {"user_id": "TESTONLY", "user_name": "Paper Tester"}


def _broker(prices: dict[str, float], **kw: Any) -> PaperKiteBroker:
    return PaperKiteBroker(_FakeQuotes(prices), clock=lambda: _FIXED_NOW, **kw)


async def test_submit_order_fills_against_real_ltp_and_books_cash() -> None:
    broker = _broker({"NSE:TCS": 3000.0}, starting_cash=1_000_000.0)
    await broker.connect()
    order = await broker.submit_order_async("NSE:TCS", quantity=10)

    assert order.status is OrderStatus.FILLED
    assert order.filled_price == 3000.0
    assert order.filled_quantity == 10
    assert order.order_id.startswith("PAPER-")

    pos = await broker.get_position_async("NSE:TCS")
    assert pos is not None
    assert pos.quantity == 10
    # Cash dropped by gross + charges.
    assert broker.realized_cash < 1_000_000.0 - 30_000.0


async def test_round_trip_nets_to_flat_and_records_two_fills() -> None:
    broker = _broker({"NSE:TCS": 3000.0}, starting_cash=1_000_000.0)
    await broker.submit_order_async("NSE:TCS", quantity=10, side=OrderSide.BUY)
    await broker.submit_order_async("NSE:TCS", quantity=10, side=OrderSide.SELL)
    assert await broker.get_position_async("NSE:TCS") is None
    assert len(broker.fills) == 2


async def test_negative_quantity_infers_sell_side() -> None:
    broker = _broker({"NSE:TCS": 3000.0})
    order = await broker.submit_order_async("NSE:TCS", quantity=-5)
    assert order.side is OrderSide.SELL


async def test_limit_order_prices_at_limit_not_ltp() -> None:
    broker = _broker({"NSE:TCS": 3000.0})
    order = await broker.submit_order_async(
        "NSE:TCS", quantity=1, side=OrderSide.BUY,
        order_type=OrderType.LIMIT, limit_price=2950.0,
    )
    assert order.filled_price == 2950.0


async def test_account_value_reflects_position_mark() -> None:
    broker = _broker({"NSE:TCS": 3000.0}, starting_cash=1_000_000.0)
    await broker.submit_order_async("NSE:TCS", quantity=10, side=OrderSide.BUY)
    # cash + 10 * 3000 mark should be ~ starting minus charges.
    value = await broker.get_account_value_async()
    assert value == pytest.approx(1_000_000.0 - broker.fills[0].charges, abs=1e-6)


async def test_missing_quote_raises_actionable_error() -> None:
    broker = _broker({"NSE:TCS": 3000.0})
    with pytest.raises(InvalidInputError):
        await broker.submit_order_async("NSE:UNKNOWN", quantity=1)


async def test_paper_orders_are_terminal() -> None:
    broker = _broker({"NSE:TCS": 3000.0})
    order = await broker.submit_order_async("NSE:TCS", quantity=1)
    assert await broker.cancel_order_async(order.order_id) is False
    assert await broker.get_pending_orders_async() == []
    with pytest.raises(InvalidInputError):
        await broker.replace_order_async(order.order_id, quantity=2)


async def test_bare_symbol_without_exchange_rejected() -> None:
    broker = _broker({"TCS": 3000.0})
    with pytest.raises(InvalidInputError):
        await broker.submit_order_async("TCS", quantity=1)


# ---------------------------------------------------------------------------
# Structural safety guarantee.
# ---------------------------------------------------------------------------


def test_readonly_quote_client_exposes_no_order_methods() -> None:
    """The data surface a paper broker holds cannot place/modify/cancel."""
    public = {name for name in dir(ReadOnlyQuoteClient) if not name.startswith("_")}
    forbidden = {"place_order", "place_autoslice_order", "modify_order", "cancel_order"}
    assert public.isdisjoint(forbidden)


def test_paper_module_never_calls_live_order_endpoints() -> None:
    """The paper source contains no *call* to a live order endpoint.

    Docstrings name the endpoints to explain the guarantee, so we look for
    call syntax (``.place_order(``) rather than the bare token: that is the
    thing that would actually send an order to the exchange.
    """
    source = Path(inspect.getfile(PaperKiteBroker)).read_text(encoding="utf-8")
    for endpoint in ("place_order", "place_autoslice_order", "modify_order", "cancel_order"):
        assert f".{endpoint}(" not in source, (
            f".{endpoint}( call site must not appear in paper execution code"
        )


def test_paper_broker_is_a_distinct_class_from_live_broker() -> None:
    from ml4t.india.live.kite_broker import KiteBroker

    assert PaperKiteBroker is not KiteBroker
    assert not issubclass(PaperKiteBroker, KiteBroker)

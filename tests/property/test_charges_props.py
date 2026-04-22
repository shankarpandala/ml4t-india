"""Property-based tests for :class:`IndianChargesModel` and
:class:`ZerodhaChargesModel`."""

from __future__ import annotations

from hypothesis import given
from hypothesis import strategies as st

from ml4t.india.backtest import IndianChargesModel, ZerodhaChargesModel
from ml4t.india.backtest.charges import Segment


@given(
    qty=st.integers(min_value=1, max_value=10_000),
    price=st.floats(min_value=0.05, max_value=100_000.0, allow_nan=False, allow_infinity=False),
    is_sell=st.booleans(),
)
def test_charges_non_negative(qty: int, price: float, is_sell: bool) -> None:
    """Charges must never be negative for any valid fill."""
    model = IndianChargesModel()
    signed_qty = -qty if is_sell else qty
    assert model.calculate("NSE:RELIANCE", signed_qty, price) >= 0


@given(
    qty=st.integers(min_value=1, max_value=10_000),
    price=st.floats(min_value=0.05, max_value=100_000.0, allow_nan=False, allow_infinity=False),
)
def test_zerodha_options_brokerage_flat_20(qty: int, price: float) -> None:
    """Options always cost Rs 20 brokerage regardless of trade size."""
    model = ZerodhaChargesModel()
    assert model._brokerage(Segment.EQUITY_OPTIONS, qty * price) == 20.0


@given(
    qty=st.integers(min_value=1, max_value=10_000),
    price=st.floats(min_value=0.05, max_value=100_000.0, allow_nan=False, allow_infinity=False),
)
def test_zerodha_cnc_delivery_brokerage_zero(qty: int, price: float) -> None:
    """Equity delivery (CNC) has zero brokerage on Zerodha."""
    model = ZerodhaChargesModel()
    assert model._brokerage(Segment.EQUITY_DELIVERY, qty * price) == 0.0


@given(
    qty=st.integers(min_value=1, max_value=10_000),
    price=st.floats(min_value=0.05, max_value=100_000.0, allow_nan=False, allow_infinity=False),
)
def test_intraday_brokerage_capped_at_20(qty: int, price: float) -> None:
    """Intraday brokerage is min(Rs 20, 0.03% of turnover)."""
    model = ZerodhaChargesModel()
    brokerage = model._brokerage(Segment.EQUITY_INTRADAY, qty * price)
    assert brokerage <= 20.0 + 1e-9
    assert brokerage >= 0.0


@given(
    qty=st.integers(min_value=1, max_value=10_000),
    price=st.floats(min_value=0.05, max_value=100_000.0, allow_nan=False, allow_infinity=False),
)
def test_charges_scale_with_turnover_for_symmetric_rates(qty: int, price: float) -> None:
    """Doubling the price doubles STT + GST + exchange + SEBI (linear in turnover).

    Brokerage can be non-linear (capped), so compare base charges with
    brokerage subtracted.
    """
    model = IndianChargesModel()
    charges_1x = model.calculate("NSE:RELIANCE", qty, price)
    charges_2x = model.calculate("NSE:RELIANCE", qty, price * 2)
    # In IndianChargesModel, brokerage is zero, so everything is linear.
    # 2x turnover -> 2x charges, within floating-point tolerance.
    assert abs(charges_2x - 2 * charges_1x) < max(0.01, charges_1x * 1e-9)

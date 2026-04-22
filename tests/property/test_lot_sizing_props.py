"""Property-based tests for :mod:`ml4t.india.backtest.lot_sizing`."""

from __future__ import annotations

import pytest
from hypothesis import given
from hypothesis import strategies as st

from ml4t.india.backtest import floor_to_lot, round_to_lot


@given(
    quantity=st.integers(min_value=1, max_value=10_000),
    lot_size=st.integers(min_value=1, max_value=500),
)
def test_round_to_lot_is_a_multiple(quantity: int, lot_size: int) -> None:
    """The rounded output must always be a multiple of ``lot_size``.

    Unless ``round_to_lot`` raises (non-zero input rounds to zero),
    the result is divisible by ``lot_size``.
    """
    try:
        result = round_to_lot(quantity, lot_size)
    except ValueError:
        # Acceptable for the zero-rounding case; nothing to assert.
        return
    assert result % lot_size == 0


@given(
    quantity=st.integers(min_value=1, max_value=10_000),
    lot_size=st.integers(min_value=1, max_value=500),
)
def test_round_to_lot_within_half_lot(quantity: int, lot_size: int) -> None:
    """|quantity - rounded| must be <= lot_size/2 (banker's rule).

    Strictly less is also fine; the inequality lets us allow the exact
    midpoint case that banker's rounding resolves either way.
    """
    try:
        result = round_to_lot(quantity, lot_size)
    except ValueError:
        return
    assert abs(result - quantity) <= lot_size / 2 + 1e-9


@given(
    quantity=st.integers(min_value=0, max_value=10_000),
    lot_size=st.integers(min_value=1, max_value=500),
)
def test_floor_is_a_multiple(quantity: int, lot_size: int) -> None:
    """floor_to_lot output is always a multiple of lot_size."""
    result = floor_to_lot(quantity, lot_size)
    assert result % lot_size == 0


@given(
    quantity=st.integers(min_value=0, max_value=10_000),
    lot_size=st.integers(min_value=1, max_value=500),
)
def test_floor_never_exceeds_input(quantity: int, lot_size: int) -> None:
    """floor_to_lot never exceeds the input quantity."""
    assert floor_to_lot(quantity, lot_size) <= quantity


@given(
    quantity=st.integers(min_value=0, max_value=10_000),
    lot_size=st.integers(min_value=1, max_value=500),
)
def test_floor_is_within_one_lot(quantity: int, lot_size: int) -> None:
    """quantity - floor(quantity) < lot_size."""
    assert quantity - floor_to_lot(quantity, lot_size) < lot_size


@given(lot_size=st.integers(max_value=0))
def test_invalid_lot_size_always_raises(lot_size: int) -> None:
    with pytest.raises(ValueError):
        round_to_lot(50, lot_size)
    with pytest.raises(ValueError):
        floor_to_lot(50, lot_size)

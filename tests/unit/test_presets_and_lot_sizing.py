"""Tests for :mod:`ml4t.india.backtest.presets` and :mod:`.lot_sizing`."""

from __future__ import annotations

import pytest
from ml4t.backtest import BacktestConfig, CommissionType

from ml4t.india.backtest import floor_to_lot, nse_india_config, round_to_lot

# ---------- preset ----------


class TestNseIndiaConfig:
    def test_returns_backtest_config(self) -> None:
        cfg = nse_india_config()
        assert isinstance(cfg, BacktestConfig)

    def test_defaults_to_percentage_commission(self) -> None:
        cfg = nse_india_config()
        assert cfg.commission_type == CommissionType.PERCENTAGE
        assert cfg.commission_rate == 0.0012
        assert cfg.slippage_rate == 0.0005

    def test_overrides_pass_through(self) -> None:
        """Explicit kwargs override defaults."""
        cfg = nse_india_config(commission_rate=0.0003, initial_cash=5_000_000.0)
        assert cfg.commission_rate == 0.0003
        # initial_cash isn't set by the preset; the override still lands.
        assert cfg.initial_cash == 5_000_000.0


# ---------- lot sizing ----------


class TestRoundToLot:
    @pytest.mark.parametrize(
        ("quantity", "lot_size", "expected"),
        [
            (50, 50, 50),  # exact multiple
            (75, 50, 100),  # rounds up at 1.5
            (74, 50, 50),  # rounds down below half
            (76, 50, 100),  # rounds up above half
            (1.0, 1, 1),  # lot_size 1 is a pass-through
            (100, 15, 105),  # 100/15=6.67 -> 7*15=105
        ],
    )
    def test_round_cases(self, quantity: float, lot_size: int, expected: int) -> None:
        assert round_to_lot(quantity, lot_size) == expected

    def test_zero_input_allowed(self) -> None:
        """Zero in, zero out -- not an error (caller explicitly flat)."""
        assert round_to_lot(0, 50) == 0

    def test_rounding_to_zero_from_nonzero_raises(self) -> None:
        """Asking for 10 units of a 50-lot instrument rounds to 0 -- error."""
        with pytest.raises(ValueError, match="produced zero"):
            round_to_lot(10, 50)

    def test_invalid_lot_size(self) -> None:
        with pytest.raises(ValueError, match="lot_size"):
            round_to_lot(50, 0)


class TestFloorToLot:
    @pytest.mark.parametrize(
        ("quantity", "lot_size", "expected"),
        [
            (100, 50, 100),  # exact
            (149, 50, 100),  # floor
            (99, 50, 50),  # floor
            (49, 50, 0),  # below lot_size
        ],
    )
    def test_floor_cases(self, quantity: float, lot_size: int, expected: int) -> None:
        assert floor_to_lot(quantity, lot_size) == expected

    def test_invalid_lot_size(self) -> None:
        with pytest.raises(ValueError, match="lot_size"):
            floor_to_lot(50, 0)

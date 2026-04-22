"""``nse_india`` convenience preset for :class:`ml4t.backtest.BacktestConfig`.

Upstream ``BacktestConfig`` configures commission via enum fields
(``commission_type``, ``commission_rate``) rather than a pluggable
model object. That precludes wiring :class:`IndianChargesModel`
directly into the config -- but the preset below sets a realistic
equity-intraday approximation so a quick backtest "looks like India"
without the caller filling in 10 fields by hand.

For exact Zerodha charges (including STT sell-side asymmetry, GST on
brokerage, exchange turnover per-segment), use
:class:`~ml4t.india.backtest.charges.ZerodhaChargesModel` on the
engine's fill output downstream.
"""

from __future__ import annotations

from typing import Any

from ml4t.backtest import BacktestConfig, CommissionType


def nse_india_config(**overrides: Any) -> BacktestConfig:
    """Return a :class:`BacktestConfig` preset for Indian retail equity.

    Fields set:

    * ``commission_type = PERCENTAGE``, ``commission_rate = 0.0012``
      (blended Zerodha intraday: brokerage + STT-half + GST + exchange).
    * ``slippage_rate = 0.0005`` (5 bps -- conservative for large-cap NSE).
    * ``stop_slippage_rate = 0.001`` (stops fill worse under stress).

    Additional fields pass through ``overrides`` so callers can tweak
    one knob without rebuilding the whole config.
    """
    defaults: dict[str, Any] = {
        "commission_type": CommissionType.PERCENTAGE,
        "commission_rate": 0.0012,
        "slippage_rate": 0.0005,
        "stop_slippage_rate": 0.001,
    }
    defaults.update(overrides)
    return BacktestConfig(**defaults)


__all__ = ["nse_india_config"]

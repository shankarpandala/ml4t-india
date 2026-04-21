""":mod:`ml4t.india.backtest` -- Indian-market charges + presets for ml4t-backtest.

Contributes only what upstream ml4t-backtest does not know about Indian
regulation: STT, GST, SEBI turnover, state stamp duty, exchange turnover,
and Zerodha's specific brokerage schedule.

Everything else (Engine, Strategy, risk rules, preset registry) is
consumed from upstream unchanged.
"""

from __future__ import annotations

from ml4t.india.backtest.charges import (
    IndianChargesModel,
    ZerodhaChargesModel,
)

__all__ = [
    "IndianChargesModel",
    "ZerodhaChargesModel",
]

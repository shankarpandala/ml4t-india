""":mod:`ml4t.india.options` -- option-chain + Greeks for Indian F&O.

NFO (NSE F&O) and BFO (BSE F&O) options are the Phase-5 target. The
building blocks are:

* :class:`OptionChain` -- a snapshot of every strike for a given
  underlying + expiry, indexed so ATM lookup + moneyness filters are
  O(log N).
* :func:`compute_greeks` -- Black-Scholes delta / gamma / vega / theta
  using :mod:`py_vollib` if installed, otherwise a numpy fallback.

Both are deliberately offline: they consume instrument dumps +
option LTPs (already owned by :class:`KiteProvider` / :class:`KiteClient`),
not the network.
"""

from __future__ import annotations

from ml4t.india.options.chain import OptionChain, OptionContract
from ml4t.india.options.greeks import Greeks, compute_greeks

__all__ = [
    "Greeks",
    "OptionChain",
    "OptionContract",
    "compute_greeks",
]

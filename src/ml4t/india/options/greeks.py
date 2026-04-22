"""Black-Scholes Greeks for Indian equity options.

Computes delta / gamma / vega / theta / rho for a European call or
put under standard Black-Scholes assumptions. Implied volatility is
exposed separately because its root-finder belongs to the
:mod:`py_vollib` dependency path.

Two code paths:

* :mod:`py_vollib.black_scholes.greeks.analytical` if installed.
  Documented as the accurate baseline against which our fallback is
  tested. Installed via the ``ml4t-india[options]`` extra.

* A numpy + scipy.stats fallback, for environments (including Phase-0
  CI lanes) that do not install ``py_vollib``. The fallback uses the
  same analytical formulas -- math is identical, only the dependency
  graph differs.

Inputs are SI units (spot in INR, strike in INR, time in years,
risk-free rate as a decimal e.g. 0.07, vol as a decimal e.g. 0.20).
No conversion for Indian-market conventions is applied here; the
chain layer is agnostic.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Literal

OptionType = Literal["CE", "PE"]


@dataclass(frozen=True, slots=True)
class Greeks:
    """Black-Scholes Greeks, per unit of the underlying.

    Fields match the standard convention used by py_vollib. Theta is
    expressed PER-YEAR (not per-day); callers wanting daily decay
    divide by 365.
    """

    delta: float
    gamma: float
    vega: float
    theta: float
    rho: float


try:
    from py_vollib.black_scholes.greeks.analytical import (
        delta as _pv_delta,
    )
    from py_vollib.black_scholes.greeks.analytical import (
        gamma as _pv_gamma,
    )
    from py_vollib.black_scholes.greeks.analytical import (
        rho as _pv_rho,
    )
    from py_vollib.black_scholes.greeks.analytical import (
        theta as _pv_theta,
    )
    from py_vollib.black_scholes.greeks.analytical import (
        vega as _pv_vega,
    )

    _PYVOLLIB_AVAILABLE = True
except ImportError:
    _PYVOLLIB_AVAILABLE = False


def _norm_cdf(x: float) -> float:
    """Standard normal CDF via :func:`math.erf` (no scipy dependency)."""
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def _norm_pdf(x: float) -> float:
    return math.exp(-0.5 * x * x) / math.sqrt(2.0 * math.pi)


def _validate(S: float, K: float, t: float, r: float, sigma: float) -> None:
    if S <= 0 or K <= 0:
        raise ValueError(f"S and K must be > 0 (got S={S}, K={K})")
    if t <= 0:
        raise ValueError(f"t (time to expiry, years) must be > 0 (got {t})")
    if sigma <= 0:
        raise ValueError(f"sigma (vol) must be > 0 (got {sigma})")
    # r may be negative (rate cuts) but rule out NaN / inf.
    if not math.isfinite(r):
        raise ValueError(f"r must be finite (got {r})")


def _compute_greeks_numpy(
    flag: OptionType,
    S: float,
    K: float,
    t: float,
    r: float,
    sigma: float,
) -> Greeks:
    """Closed-form BS Greeks; returns the same shape as py_vollib analytical."""
    _validate(S, K, t, r, sigma)
    sqrt_t = math.sqrt(t)
    d1 = (math.log(S / K) + (r + 0.5 * sigma * sigma) * t) / (sigma * sqrt_t)
    d2 = d1 - sigma * sqrt_t
    nd1 = _norm_cdf(d1)
    nd2 = _norm_cdf(d2)
    pdf_d1 = _norm_pdf(d1)
    disc = math.exp(-r * t)

    if flag == "CE":
        delta = nd1
        theta_per_year = -(S * pdf_d1 * sigma) / (2.0 * sqrt_t) - r * K * disc * nd2
        rho = K * t * disc * nd2
    else:  # PE
        delta = nd1 - 1.0
        theta_per_year = -(S * pdf_d1 * sigma) / (2.0 * sqrt_t) + r * K * disc * (1.0 - nd2)
        rho = -K * t * disc * (1.0 - nd2)

    gamma = pdf_d1 / (S * sigma * sqrt_t)
    vega = S * pdf_d1 * sqrt_t
    return Greeks(
        delta=delta,
        gamma=gamma,
        vega=vega,
        theta=theta_per_year,
        rho=rho,
    )


def _compute_greeks_pyvollib(
    flag: OptionType,
    S: float,
    K: float,
    t: float,
    r: float,
    sigma: float,
) -> Greeks:
    """Forward to py_vollib analytical formulas.

    py_vollib uses ``'c'`` / ``'p'`` flags rather than ``'CE'`` / ``'PE'``
    and returns theta PER-YEAR already. Note vega is per 1.0 vol-point,
    and theta is per YEAR (not per day, unlike py_vollib's default
    annual/day scaling which we explicitly avoid).
    """
    _validate(S, K, t, r, sigma)
    pv_flag = "c" if flag == "CE" else "p"
    return Greeks(
        delta=float(_pv_delta(pv_flag, S, K, t, r, sigma)),
        gamma=float(_pv_gamma(pv_flag, S, K, t, r, sigma)),
        # py_vollib's vega/theta divide by 100 / 365 by default; undo so
        # we return raw BS. Library convention is documented in its docs.
        vega=float(_pv_vega(pv_flag, S, K, t, r, sigma)) * 100.0,
        theta=float(_pv_theta(pv_flag, S, K, t, r, sigma)) * 365.0,
        rho=float(_pv_rho(pv_flag, S, K, t, r, sigma)) * 100.0,
    )


def compute_greeks(
    flag: OptionType,
    spot: float,
    strike: float,
    time_to_expiry: float,
    risk_free_rate: float,
    volatility: float,
    *,
    prefer_pyvollib: bool = True,
) -> Greeks:
    """Return the Black-Scholes Greeks for a European option.

    Parameters
    ----------
    flag:
        ``"CE"`` (call) or ``"PE"`` (put).
    spot:
        Underlying spot in INR.
    strike:
        Option strike in INR.
    time_to_expiry:
        Time to expiry in YEARS (e.g. 21 calendar days / 365).
    risk_free_rate:
        Annual risk-free rate as a decimal (e.g. ``0.07`` for 7%).
    volatility:
        Annual volatility as a decimal (e.g. ``0.20`` for 20%).
    prefer_pyvollib:
        If ``True`` (default) and :mod:`py_vollib` is installed, use its
        analytical Greeks. Otherwise use the numpy fallback (identical
        math). Set ``False`` to force the fallback -- mostly useful for
        tests that pin exact values regardless of library availability.

    Raises
    ------
    ValueError
        If inputs violate BS preconditions (S, K, t, sigma must be > 0;
        r must be finite).
    """
    if flag not in ("CE", "PE"):
        raise ValueError(f"flag must be 'CE' or 'PE', got {flag!r}")
    if prefer_pyvollib and _PYVOLLIB_AVAILABLE:
        return _compute_greeks_pyvollib(
            flag, spot, strike, time_to_expiry, risk_free_rate, volatility
        )
    return _compute_greeks_numpy(flag, spot, strike, time_to_expiry, risk_free_rate, volatility)


__all__ = ["Greeks", "compute_greeks"]

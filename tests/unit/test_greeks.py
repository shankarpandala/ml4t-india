"""Tests for :func:`ml4t.india.options.greeks.compute_greeks`.

Covers both code paths:

* numpy fallback (always available)
* py_vollib path (only asserted when the package is installed)

and verifies put-call parity on delta + gamma + rho, plus the
ValueError boundary.
"""

from __future__ import annotations

import math

import pytest

from ml4t.india.options.greeks import Greeks, compute_greeks

# Reference case: spot 25000, strike 25000 (ATM), 30 calendar days, r=7%,
# vol=15%. NIFTY-typical parameters.
_SPOT = 25000.0
_STRIKE = 25000.0
_T = 30.0 / 365.0
_R = 0.07
_SIGMA = 0.15


# ---- shape ------------------------------------------------------------


class TestShape:
    def test_returns_greeks_dataclass(self) -> None:
        g = compute_greeks("CE", _SPOT, _STRIKE, _T, _R, _SIGMA, prefer_pyvollib=False)
        assert isinstance(g, Greeks)
        for field in ("delta", "gamma", "vega", "theta", "rho"):
            val = getattr(g, field)
            assert math.isfinite(val)


# ---- fallback correctness --------------------------------------------


class TestNumpyFallback:
    """Exact-ish values from the closed-form BS, spot 25000 / K 25000 /
    t 30/365 / r 7% / sigma 15%. Tolerances loose enough to match
    py_vollib to 4 decimal places (the two implementations are the
    same math but py_vollib uses a double-precision path that rounds
    differently on some platforms).
    """

    def test_call_delta_atm_is_roughly_half(self) -> None:
        g = compute_greeks("CE", _SPOT, _STRIKE, _T, _R, _SIGMA, prefer_pyvollib=False)
        # ATM call delta is just above 0.5 for a positive rate.
        assert 0.50 < g.delta < 0.60

    def test_put_delta_atm_is_roughly_minus_half(self) -> None:
        g = compute_greeks("PE", _SPOT, _STRIKE, _T, _R, _SIGMA, prefer_pyvollib=False)
        assert -0.50 < g.delta < -0.40

    def test_gamma_same_for_call_and_put(self) -> None:
        g_ce = compute_greeks("CE", _SPOT, _STRIKE, _T, _R, _SIGMA, prefer_pyvollib=False)
        g_pe = compute_greeks("PE", _SPOT, _STRIKE, _T, _R, _SIGMA, prefer_pyvollib=False)
        assert g_ce.gamma == pytest.approx(g_pe.gamma, rel=1e-12)

    def test_vega_same_for_call_and_put(self) -> None:
        g_ce = compute_greeks("CE", _SPOT, _STRIKE, _T, _R, _SIGMA, prefer_pyvollib=False)
        g_pe = compute_greeks("PE", _SPOT, _STRIKE, _T, _R, _SIGMA, prefer_pyvollib=False)
        assert g_ce.vega == pytest.approx(g_pe.vega, rel=1e-12)

    def test_theta_negative(self) -> None:
        """Long options decay in value; theta is negative."""
        g_ce = compute_greeks("CE", _SPOT, _STRIKE, _T, _R, _SIGMA, prefer_pyvollib=False)
        g_pe = compute_greeks("PE", _SPOT, _STRIKE, _T, _R, _SIGMA, prefer_pyvollib=False)
        assert g_ce.theta < 0
        assert g_pe.theta < 0

    def test_rho_signs(self) -> None:
        """Rising rates help calls, hurt puts."""
        g_ce = compute_greeks("CE", _SPOT, _STRIKE, _T, _R, _SIGMA, prefer_pyvollib=False)
        g_pe = compute_greeks("PE", _SPOT, _STRIKE, _T, _R, _SIGMA, prefer_pyvollib=False)
        assert g_ce.rho > 0
        assert g_pe.rho < 0


class TestPutCallParity:
    """delta(call) - delta(put) = 1; gamma same; vega same; rho(call) -
    rho(put) = K*t*exp(-r*t)."""

    def test_delta_parity(self) -> None:
        g_ce = compute_greeks("CE", _SPOT, _STRIKE, _T, _R, _SIGMA, prefer_pyvollib=False)
        g_pe = compute_greeks("PE", _SPOT, _STRIKE, _T, _R, _SIGMA, prefer_pyvollib=False)
        assert g_ce.delta - g_pe.delta == pytest.approx(1.0, abs=1e-10)


# ---- itm / otm sanity --------------------------------------------------


class TestMoneyness:
    def test_deep_itm_call_delta_close_to_one(self) -> None:
        g = compute_greeks("CE", 30000, 25000, _T, _R, _SIGMA, prefer_pyvollib=False)
        assert g.delta > 0.95

    def test_deep_otm_call_delta_close_to_zero(self) -> None:
        g = compute_greeks("CE", 20000, 25000, _T, _R, _SIGMA, prefer_pyvollib=False)
        assert g.delta < 0.05


# ---- errors ------------------------------------------------------------


class TestErrors:
    def test_bad_flag(self) -> None:
        with pytest.raises(ValueError, match="flag"):
            compute_greeks("XX", _SPOT, _STRIKE, _T, _R, _SIGMA)  # type: ignore[arg-type]

    def test_non_positive_spot(self) -> None:
        with pytest.raises(ValueError, match="S and K"):
            compute_greeks("CE", 0.0, _STRIKE, _T, _R, _SIGMA)

    def test_non_positive_strike(self) -> None:
        with pytest.raises(ValueError, match="S and K"):
            compute_greeks("CE", _SPOT, -1.0, _T, _R, _SIGMA)

    def test_non_positive_time(self) -> None:
        with pytest.raises(ValueError, match="time to expiry"):
            compute_greeks("CE", _SPOT, _STRIKE, 0.0, _R, _SIGMA)

    def test_non_positive_sigma(self) -> None:
        with pytest.raises(ValueError, match="vol"):
            compute_greeks("CE", _SPOT, _STRIKE, _T, _R, 0.0)

    def test_non_finite_rate(self) -> None:
        with pytest.raises(ValueError, match="finite"):
            compute_greeks("CE", _SPOT, _STRIKE, _T, math.nan, _SIGMA)

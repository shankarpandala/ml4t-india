"""Property-based tests for :func:`ml4t.india.options.greeks.compute_greeks`."""

from __future__ import annotations

import math

from hypothesis import given
from hypothesis import strategies as st

from ml4t.india.options.greeks import compute_greeks

# Restrict inputs to a sensible quant range. Black-Scholes explodes at
# extreme tails that don't correspond to anything we'd trade.
_spot = st.floats(min_value=100.0, max_value=100_000.0, allow_nan=False, allow_infinity=False)
_strike = st.floats(min_value=100.0, max_value=100_000.0, allow_nan=False, allow_infinity=False)
_t = st.floats(min_value=1.0 / 365, max_value=2.0, allow_nan=False, allow_infinity=False)
_r = st.floats(min_value=-0.02, max_value=0.20, allow_nan=False, allow_infinity=False)
_sigma = st.floats(min_value=0.01, max_value=2.0, allow_nan=False, allow_infinity=False)


@given(_spot, _strike, _t, _r, _sigma)
def test_call_delta_in_unit_interval(S: float, K: float, t: float, r: float, sigma: float) -> None:
    g = compute_greeks("CE", S, K, t, r, sigma, prefer_pyvollib=False)
    assert 0.0 <= g.delta <= 1.0


@given(_spot, _strike, _t, _r, _sigma)
def test_put_delta_in_unit_interval(S: float, K: float, t: float, r: float, sigma: float) -> None:
    g = compute_greeks("PE", S, K, t, r, sigma, prefer_pyvollib=False)
    assert -1.0 <= g.delta <= 0.0


@given(_spot, _strike, _t, _r, _sigma)
def test_gamma_non_negative(S: float, K: float, t: float, r: float, sigma: float) -> None:
    g = compute_greeks("CE", S, K, t, r, sigma, prefer_pyvollib=False)
    assert g.gamma >= 0.0


@given(_spot, _strike, _t, _r, _sigma)
def test_vega_non_negative(S: float, K: float, t: float, r: float, sigma: float) -> None:
    g = compute_greeks("CE", S, K, t, r, sigma, prefer_pyvollib=False)
    assert g.vega >= 0.0


@given(_spot, _strike, _t, _r, _sigma)
def test_put_call_delta_parity(S: float, K: float, t: float, r: float, sigma: float) -> None:
    """delta(call) - delta(put) = 1 exactly for European options under BS."""
    gc = compute_greeks("CE", S, K, t, r, sigma, prefer_pyvollib=False)
    gp = compute_greeks("PE", S, K, t, r, sigma, prefer_pyvollib=False)
    assert math.isclose(gc.delta - gp.delta, 1.0, abs_tol=1e-10)


@given(_spot, _strike, _t, _r, _sigma)
def test_put_call_gamma_equal(S: float, K: float, t: float, r: float, sigma: float) -> None:
    gc = compute_greeks("CE", S, K, t, r, sigma, prefer_pyvollib=False)
    gp = compute_greeks("PE", S, K, t, r, sigma, prefer_pyvollib=False)
    assert math.isclose(gc.gamma, gp.gamma, rel_tol=1e-12)


@given(_spot, _strike, _t, _r, _sigma)
def test_greeks_all_finite(S: float, K: float, t: float, r: float, sigma: float) -> None:
    g = compute_greeks("CE", S, K, t, r, sigma, prefer_pyvollib=False)
    for field in ("delta", "gamma", "vega", "theta", "rho"):
        assert math.isfinite(getattr(g, field))

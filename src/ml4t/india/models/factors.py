"""NSE-aware preset constructors for upstream latent-factor models.

The factory functions below wrap :class:`ml4t.models.PCAModel`,
:class:`~ml4t.models.RPPCAModel` and :class:`~ml4t.models.IPCAModel`
with sensible defaults for Indian equity panels:

* ``standardize=True`` -- INR returns can swing several percentage
  points within a session for SME and microcap names; standardising
  makes the loadings comparable across sectors.
* ``demean=True`` -- index-relative factor structure dominates Indian
  cross-sections; demeaning removes the level shock from market-wide
  events (e.g., budget day, RBI policy).
* Default factor counts pinned at 5 -- matches the standard Indian
  Fama-French 4-factor + market published by IIM-A; one extra factor
  absorbs the "FII flow" component that is well-documented in the
  Indian academic literature.

``ml4t.models`` is imported lazily inside each function so callers
that only use ``ml4t.india.live`` (the broker layer) don't pay the
upstream import cost.
"""

from __future__ import annotations

from typing import Any


def nse_pca_model(
    n_factors: int = 5,
    *,
    standardize: bool = True,
    demean: bool = True,
    **overrides: Any,
) -> Any:
    """Build a :class:`~ml4t.models.PCAModel` configured for NSE panels.

    Parameters
    ----------
    n_factors:
        Number of latent factors. Default 5 (market + SMB + HML + MOM
        + FII-flow proxy).
    standardize, demean:
        Defaults match the Indian academic literature; override only
        if you understand the downstream effect on factor identifiability.
    overrides:
        Forwarded to ``PCAModel(...)``. Use to set ``min_obs``,
        ``max_iter``, etc.
    """
    from ml4t.models import PCAModel

    config: dict[str, Any] = {
        "n_factors": n_factors,
        "standardize": standardize,
        "demean": demean,
        **overrides,
    }
    return PCAModel(**config)


def nse_rppca_model(
    n_factors: int = 5,
    *,
    risk_premium_weight: float = 10.0,
    standardize: bool = True,
    **overrides: Any,
) -> Any:
    """Risk-Premium PCA preset for NSE.

    The default ``risk_premium_weight=10`` follows Lettau & Pelger
    (2020); the weight controls how much the optimisation prefers
    factors that are cross-sectionally priced over factors that only
    explain variance. For Indian markets, where idiosyncratic noise
    is high relative to factor risk premia, a large weight is needed
    to recover identifiable factors.
    """
    from ml4t.models import RPPCAModel

    config: dict[str, Any] = {
        "n_factors": n_factors,
        "risk_premium_weight": risk_premium_weight,
        "standardize": standardize,
        **overrides,
    }
    return RPPCAModel(**config)


def nse_ipca_model(
    n_factors: int = 5,
    *,
    n_chars: int | None = None,
    **overrides: Any,
) -> Any:
    """Instrumented PCA preset for NSE.

    IPCA conditions factor loadings on observable characteristics
    (book/market, size, momentum, ...). For NSE, the canonical
    characteristic set is BTM, ME, MOM(12), STREV(1), and MAX(1)
    (peak intraday return) -- 5 chars by default; override
    ``n_chars`` if your characteristic frame has a different width.
    """
    from ml4t.models import IPCAModel

    config: dict[str, Any] = {
        "n_factors": n_factors,
        **overrides,
    }
    if n_chars is not None:
        config["n_chars"] = n_chars
    return IPCAModel(**config)


__all__ = [
    "nse_ipca_model",
    "nse_pca_model",
    "nse_rppca_model",
]

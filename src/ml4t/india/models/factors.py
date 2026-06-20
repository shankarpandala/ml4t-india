"""NSE-aware preset constructors for upstream latent-factor models.

The factory functions below wrap :class:`ml4t.models.PCAModel`,
:class:`~ml4t.models.RPPCAModel` and :class:`~ml4t.models.IPCAModel`
with sensible defaults for Indian equity panels:

* Default factor counts pinned at 5 -- matches the standard Indian
  Fama-French 4-factor + market published by IIM-A; one extra factor
  absorbs the "FII flow" component that is well-documented in the
  Indian academic literature.

Upstream ``ml4t.models`` constructs each model from a typed *config*
dataclass (``PCAConfig``/``RPPCAConfig``/``IPCAConfig``) rather than
loose keyword arguments. These wrappers therefore translate the
India-flavoured parameters into the matching config object:

* ``n_factors`` -> ``<Config>.n_factors`` (unchanged name).
* ``risk_premium_weight`` -> ``RPPCAConfig.gamma`` (the Lettau & Pelger
  risk-premium weight; renamed upstream).

Any extra ``**overrides`` are forwarded verbatim to the config
constructor, so callers can set real config fields (``seed``,
``device``, ``dtype``, ``max_iter``, ``tol``, ...); an unknown field
raises loudly rather than being silently ignored.

``ml4t.models`` is imported lazily inside each function so callers
that only use ``ml4t.india.live`` (the broker layer) don't pay the
upstream import cost.
"""

from __future__ import annotations

from typing import Any


def nse_pca_model(n_factors: int = 5, **overrides: Any) -> Any:
    """Build a :class:`~ml4t.models.PCAModel` configured for NSE panels.

    Parameters
    ----------
    n_factors:
        Number of latent factors. Default 5 (market + SMB + HML + MOM
        + FII-flow proxy). Maps to :class:`~ml4t.models.PCAConfig`'s
        ``n_factors`` field.
    overrides:
        Forwarded to :class:`~ml4t.models.PCAConfig`. Use to set
        ``seed``, ``device``, ``dtype``, ``persistent_entities``, etc.
    """
    from ml4t.models import PCAConfig, PCAModel

    return PCAModel(PCAConfig(n_factors=n_factors, **overrides))


def nse_rppca_model(
    n_factors: int = 5,
    *,
    risk_premium_weight: float = 10.0,
    **overrides: Any,
) -> Any:
    """Risk-Premium PCA preset for NSE.

    The default ``risk_premium_weight=10`` follows Lettau & Pelger
    (2020); the weight controls how much the optimisation prefers
    factors that are cross-sectionally priced over factors that only
    explain variance. For Indian markets, where idiosyncratic noise
    is high relative to factor risk premia, a large weight is needed
    to recover identifiable factors.

    ``risk_premium_weight`` maps to :class:`~ml4t.models.RPPCAConfig`'s
    ``gamma`` field (the upstream name for the same quantity).
    """
    from ml4t.models import RPPCAConfig, RPPCAModel

    return RPPCAModel(
        RPPCAConfig(n_factors=n_factors, gamma=risk_premium_weight, **overrides)
    )


def nse_ipca_model(n_factors: int = 5, **overrides: Any) -> Any:
    """Instrumented PCA preset for NSE.

    IPCA conditions factor loadings on observable characteristics
    (book/market, size, momentum, ...). For NSE, the canonical
    characteristic set is BTM, ME, MOM(12), STREV(1), and MAX(1)
    (peak intraday return). The characteristic width is inferred from
    the input frame upstream, so it is no longer a constructor knob.

    Parameters
    ----------
    n_factors:
        Number of latent factors. Maps to
        :class:`~ml4t.models.IPCAConfig`'s ``n_factors`` field.
    overrides:
        Forwarded to :class:`~ml4t.models.IPCAConfig`. Use to set
        ``max_iter``, ``tol``, ``factor_ridge``, ``gamma_ridge``, etc.
    """
    from ml4t.models import IPCAConfig, IPCAModel

    return IPCAModel(IPCAConfig(n_factors=n_factors, **overrides))


__all__ = [
    "nse_ipca_model",
    "nse_pca_model",
    "nse_rppca_model",
]

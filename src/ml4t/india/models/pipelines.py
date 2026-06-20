"""India-flavored presets for upstream model pipelines.

The two factories below construct
:class:`~ml4t.models.LatentFactorForecastPipeline` and
:class:`~ml4t.models.PortfolioAllocationPipeline` with NSE-aware
defaults: 5-factor model, 20-day expanding-mean forecaster, and
``BacktestDataFeedInputs`` configured for the
:func:`ml4t.india.backtest.nse_india_config` preset.

Both factories accept ``**overrides`` so callers can swap any
component without rebuilding the whole config.
"""

from __future__ import annotations

from typing import Any

from ml4t.india.models.factors import nse_pca_model


def nse_latent_factor_pipeline(
    n_factors: int = 5,
    *,
    model: Any | None = None,
    forecaster: Any | None = None,
    mapper: Any | None = None,
    **overrides: Any,
) -> Any:
    """Return a :class:`LatentFactorForecastPipeline` for NSE.

    Parameters
    ----------
    n_factors:
        Forwarded to the default model (PCA) when ``model`` is None.
    model:
        Pre-built latent-factor model. Defaults to
        :func:`nse_pca_model(n_factors)`.
    forecaster:
        Pre-built factor forecaster. Defaults to
        :class:`~ml4t.models.ExpandingMeanFactorForecaster` (a pure
        expanding mean; the upstream estimator no longer takes a fixed
        window).
    mapper:
        Pre-built :class:`~ml4t.models.AssetMapper` projecting factor
        forecasts back onto assets. Required by the upstream pipeline;
        defaults to :class:`~ml4t.models.BetaLambdaMapper`.
    overrides:
        Forwarded verbatim to the pipeline constructor.
    """
    from ml4t.models import (
        BetaLambdaMapper,
        ExpandingMeanFactorForecaster,
        LatentFactorForecastPipeline,
    )

    model = model if model is not None else nse_pca_model(n_factors=n_factors)
    forecaster = (
        forecaster if forecaster is not None else ExpandingMeanFactorForecaster()
    )
    mapper = mapper if mapper is not None else BetaLambdaMapper()
    return LatentFactorForecastPipeline(
        model=model,
        forecaster=forecaster,
        mapper=mapper,
        **overrides,
    )


def nse_portfolio_allocation_pipeline(
    *,
    portfolio_model: Any | None = None,
    postprocessor: Any | None = None,
    **overrides: Any,
) -> Any:
    """Return a :class:`PortfolioAllocationPipeline` for NSE.

    Parameters
    ----------
    portfolio_model:
        Pre-built portfolio model (Linear / LSTM / DeepPortfolio).
        Required -- pipelines are model-driven, so we don't ship a
        default. The :mod:`~ml4t.india.models.registry` exposes
        canonical India presets.
    postprocessor:
        Optional :class:`WeightConstraintPostprocessor` for max-weight
        / sector-cap / lot-size enforcement. Defaults to ``None``
        (no post-processing).
    overrides:
        Forwarded verbatim to the pipeline constructor.
    """
    from ml4t.models import PortfolioAllocationPipeline

    if portfolio_model is None:
        raise ValueError(
            "nse_portfolio_allocation_pipeline requires a portfolio_model. "
            "Use ml4t.india.models.registry.resolve_preset(...) for "
            "canonical India regimes."
        )

    config: dict[str, Any] = {
        "model": portfolio_model,
        **overrides,
    }
    if postprocessor is not None:
        config["postprocessor"] = postprocessor
    return PortfolioAllocationPipeline(**config)


__all__ = [
    "nse_latent_factor_pipeline",
    "nse_portfolio_allocation_pipeline",
]

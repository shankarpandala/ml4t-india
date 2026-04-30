""":mod:`ml4t.india.models` -- Indian-market presets for ``ml4t.models``.

The upstream ``ml4t-models`` library ships finance-native model
families (latent-factor: PCA / RPPCA / IPCA / CAE / SAE; SDF
estimator; portfolio learners: Linear / LSTM / DeepPortfolio) with
finance-shaped data contracts (``PersistentPanelBatch``,
``CrossSectionBatch``, ``PortfolioSequenceBatch``). It is consumed,
not forked.

This package contributes only Indian-market specifics on top:

* :mod:`~ml4t.india.models.factors` -- NSE-aware preset constructors
  for latent-factor models (sector dummies for NIFTY 50/500,
  INR-denominated returns, IST timezone).
* :mod:`~ml4t.india.models.labels` -- F&O-aware label generators
  feeding :class:`~ml4t.models.CrossSectionBatch` (expiry-rolled
  futures returns, lot-size-normalized cross-sections).
* :mod:`~ml4t.india.models.pipelines` -- India-flavored presets for
  :class:`~ml4t.models.LatentFactorForecastPipeline` and
  :class:`~ml4t.models.PortfolioAllocationPipeline`.
* :mod:`~ml4t.india.models.registry` -- thin registry pinning model
  + forecaster + ``BacktestDataFeedInputs`` configs for common
  India regimes (cash-equity long-only, F&O delta-neutral, sector
  rotation).

Imports from ``ml4t.models`` happen lazily inside each submodule so
the heavyweight torch dependency only loads when a model is actually
constructed.
"""

from __future__ import annotations

from ml4t.india.models.factors import (
    nse_ipca_model,
    nse_pca_model,
    nse_rppca_model,
)
from ml4t.india.models.labels import (
    ExpiryRolledFuturesLabeler,
    LotSizeNormalizedLabeler,
)
from ml4t.india.models.pipelines import (
    nse_latent_factor_pipeline,
    nse_portfolio_allocation_pipeline,
)
from ml4t.india.models.registry import (
    IndiaModelPreset,
    list_presets,
    register_preset,
    resolve_preset,
)

__all__ = [
    "ExpiryRolledFuturesLabeler",
    "IndiaModelPreset",
    "LotSizeNormalizedLabeler",
    "list_presets",
    "nse_ipca_model",
    "nse_latent_factor_pipeline",
    "nse_pca_model",
    "nse_portfolio_allocation_pipeline",
    "nse_rppca_model",
    "register_preset",
    "resolve_preset",
]

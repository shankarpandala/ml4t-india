"""Registry of canonical India model presets.

A preset is the smallest set of choices that distinguishes one
strategy regime from another:

* The model family (PCA / RPPCA / IPCA / Linear / LSTM).
* The forecaster (AR1 / EWMA / expanding-mean).
* The postprocessor (weight constraints, lot-size rounding).
* A short human-readable description.

Three presets ship out of the box:

* ``nse_cash_long_only`` -- 5-factor PCA + expanding-mean forecast,
  long-only weights, integer-share rounding.
* ``nse_fno_delta_neutral`` -- 7-factor RPPCA + AR1 forecast,
  delta-neutral weights, lot-size enforcement.
* ``nse_sector_rotation`` -- IPCA conditioned on sector dummies,
  EWMA forecast, sector-cap postprocessor.

Callers register additional presets via :func:`register_preset`.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from ml4t.india.core.exceptions import InvalidInputError


@dataclass(frozen=True, slots=True)
class IndiaModelPreset:
    """Canonical preset for an Indian-market modeling regime.

    Attributes
    ----------
    name:
        Stable identifier (e.g., ``"nse_cash_long_only"``).
    description:
        One-paragraph human description.
    pipeline_factory:
        Zero-arg callable returning a configured pipeline. Lazy so
        ``ml4t.models`` isn't imported until the preset is resolved.
    feed_inputs_factory:
        Zero-arg callable returning a ``BacktestDataFeedInputs``
        instance for the regime.
    """

    name: str
    description: str
    pipeline_factory: Callable[[], Any]
    feed_inputs_factory: Callable[[], Any]
    metadata: dict[str, Any] = field(default_factory=dict)


# Registry storage. Populated below + via register_preset().
_REGISTRY: dict[str, IndiaModelPreset] = {}


def register_preset(preset: IndiaModelPreset) -> None:
    """Register a new preset; raises if ``preset.name`` is already taken."""
    if preset.name in _REGISTRY:
        raise InvalidInputError(
            f"preset {preset.name!r} already registered; "
            "names must be unique"
        )
    _REGISTRY[preset.name] = preset


def list_presets() -> list[str]:
    """Return registered preset names, sorted."""
    return sorted(_REGISTRY)


def resolve_preset(name: str) -> IndiaModelPreset:
    """Return the preset for ``name``; raises if unknown."""
    if name not in _REGISTRY:
        raise InvalidInputError(
            f"no preset named {name!r}; "
            f"known presets: {sorted(_REGISTRY)}"
        )
    return _REGISTRY[name]


# ---- built-in presets --------------------------------------------------
#
# Each preset's factories are deliberately closures that import upstream
# `ml4t.models` lazily. This keeps `import ml4t.india.models.registry`
# free of the heavy torch dependency until a caller actually constructs
# a model.


def _nse_cash_long_only_pipeline() -> Any:
    from ml4t.india.models.pipelines import nse_latent_factor_pipeline

    return nse_latent_factor_pipeline(n_factors=5, forecaster_window=20)


def _nse_cash_long_only_feed_inputs() -> Any:
    from ml4t.models.integration import BacktestDataFeedInputs

    # Long-only equity cash universe; strategy will allocate via
    # WeightsFrame produced by the upstream pipeline.
    return BacktestDataFeedInputs()


def _nse_fno_delta_neutral_pipeline() -> Any:
    from ml4t.india.models.factors import nse_rppca_model
    from ml4t.india.models.pipelines import nse_latent_factor_pipeline

    return nse_latent_factor_pipeline(
        model=nse_rppca_model(n_factors=7, risk_premium_weight=15.0),
        forecaster_window=10,
    )


def _nse_fno_delta_neutral_feed_inputs() -> Any:
    from ml4t.models.integration import BacktestDataFeedInputs

    return BacktestDataFeedInputs()


def _nse_sector_rotation_pipeline() -> Any:
    from ml4t.india.models.factors import nse_ipca_model
    from ml4t.india.models.pipelines import nse_latent_factor_pipeline

    return nse_latent_factor_pipeline(
        model=nse_ipca_model(n_factors=4),
        forecaster_window=60,
    )


def _nse_sector_rotation_feed_inputs() -> Any:
    from ml4t.models.integration import BacktestDataFeedInputs

    return BacktestDataFeedInputs()


_BUILT_IN_PRESETS = (
    IndiaModelPreset(
        name="nse_cash_long_only",
        description=(
            "Cash-equity long-only via 5-factor PCA + expanding-mean "
            "forecast. Suits broad NIFTY 100 / NIFTY 500 universes."
        ),
        pipeline_factory=_nse_cash_long_only_pipeline,
        feed_inputs_factory=_nse_cash_long_only_feed_inputs,
        metadata={"universe": "NIFTY_100", "rebalance": "weekly"},
    ),
    IndiaModelPreset(
        name="nse_fno_delta_neutral",
        description=(
            "F&O delta-neutral via 7-factor RPPCA + AR1 forecast. Lot-"
            "size aware; targets short-horizon (10-day) factor signals."
        ),
        pipeline_factory=_nse_fno_delta_neutral_pipeline,
        feed_inputs_factory=_nse_fno_delta_neutral_feed_inputs,
        metadata={"universe": "NFO_FUTURES", "rebalance": "daily"},
    ),
    IndiaModelPreset(
        name="nse_sector_rotation",
        description=(
            "Sector rotation via IPCA conditioned on NIFTY sector "
            "dummies + EWMA factor forecast. Quarterly rebalance."
        ),
        pipeline_factory=_nse_sector_rotation_pipeline,
        feed_inputs_factory=_nse_sector_rotation_feed_inputs,
        metadata={"universe": "NIFTY_SECTORS", "rebalance": "quarterly"},
    ),
)


for _p in _BUILT_IN_PRESETS:
    _REGISTRY[_p.name] = _p


__all__ = [
    "IndiaModelPreset",
    "list_presets",
    "register_preset",
    "resolve_preset",
]

"""Regression tests for the cross-sectional latent-factor (PCA) path.

The ``nse_cash_long_only`` preset is a PCA pipeline. PCA does not accept the
long ``(timestamp, symbol, OHLCV...)`` polars frame the provider/feature
stage emits -- it requires a stable-entity
:class:`ml4t.models.PersistentPanelBatch`. Stage 6 of
``examples/end_to_end.py`` therefore must convert the feature frame into a
panel batch (via :func:`ml4t.india.models.panel.build_persistent_panel`) and
hand it to the model through ``ResearchPipeline.run(model_input_builder=...)``.

These tests lock that contract:

* ``test_latent_factor_pipeline_rejects_raw_frame`` reproduces the original
  bug -- feeding the raw frame raises ``TypeError`` with the exact
  ``PersistentPanelBatch`` message.
* ``test_research_pipeline_runs_pca_preset_with_panel_builder`` proves the
  fix -- the same model runs cleanly once the builder is supplied.

The OHLCV is synthetic *test scaffolding* (no Kite credentials, CI-safe);
it is not mock market data in the product flow.
"""

from __future__ import annotations

import datetime as dt
import math
from typing import Any

import polars as pl
import pytest

pytest.importorskip("ml4t.models")

from ml4t.backtest import Strategy

from ml4t.india.data.base import IndianOHLCVProvider
from ml4t.india.models.panel import build_persistent_panel
from ml4t.india.models.pipelines import nse_latent_factor_pipeline
from ml4t.india.workflows.research import ResearchPipeline, ResearchPipelineResult

_SYMBOLS = ["AAA", "BBB", "CCC", "DDD"]
_BARS = 40


def _panel_frame(symbols: list[str], bars: int = _BARS) -> pl.DataFrame:
    """Long-format daily OHLCV with one distinct price path per symbol."""
    rows = []
    start = dt.date(2026, 1, 1)
    for s_idx, symbol in enumerate(symbols):
        price = 100.0 + 10.0 * s_idx
        for bar in range(bars):
            # Deterministic, symbol-specific wiggle so returns are non-trivial
            # and the cross-section is not perfectly collinear.
            price *= 1.0 + 0.002 * math.sin(bar / 3.0 + s_idx)
            rows.append(
                {
                    "timestamp": start + dt.timedelta(days=bar),
                    "symbol": symbol,
                    "open": price,
                    "high": price + 0.5,
                    "low": price - 0.5,
                    "close": price,
                    "volume": 1_000.0,
                }
            )
    return pl.DataFrame(rows)


class _FakeProvider(IndianOHLCVProvider):
    """Provider returning the canned multi-symbol panel."""

    @property
    def name(self) -> str:
        return "fake-panel"

    def fetch_ohlcv(  # type: ignore[override]  # research.py calls with plural symbols=
        self,
        symbols: list[str],
        start: Any,
        end: Any,
        frequency: str,
    ) -> pl.DataFrame:
        return _panel_frame(symbols)


class _NoopStrategy(Strategy):
    def on_data(self, timestamp, data, context, broker) -> None:  # noqa: ANN001
        return None


def test_build_persistent_panel_derives_returns_and_shape() -> None:
    """The helper pivots a long frame into a (T, N) returns panel."""
    frame = _panel_frame(_SYMBOLS)
    batch = build_persistent_panel(frame)

    from ml4t.models import PersistentPanelBatch

    assert isinstance(batch, PersistentPanelBatch)
    # N assets, T-1 finite return rows per asset (first bar is NaN).
    assert set(batch.asset_ids) == set(_SYMBOLS)
    assert batch.returns is not None
    assert batch.returns.shape == (_BARS, len(_SYMBOLS))


def test_latent_factor_pipeline_rejects_raw_frame() -> None:
    """Without the panel builder the PCA model raises the original error.

    This is the exact failure stage 6 hit: ``model.fit(raw_frame)`` ->
    ``TypeError: PCA requires PersistentPanelBatch input``.
    """
    pipeline_model = nse_latent_factor_pipeline(n_factors=3)
    with pytest.raises(TypeError, match="PersistentPanelBatch"):
        pipeline_model.fit(_panel_frame(_SYMBOLS))


def test_research_pipeline_runs_pca_preset_with_panel_builder() -> None:
    """run() drives a real PCA pipeline once the panel builder is wired.

    Mirrors ``examples/end_to_end.py`` stage 6: resolve the model, then run
    the research pipeline with ``model_input_builder`` building the panel
    batch from the feature frame. The PCA fit/predict must succeed and
    populate ``model_outputs`` -- no ``PersistentPanelBatch`` TypeError.
    """
    pipeline = ResearchPipeline(provider=_FakeProvider())
    model = nse_latent_factor_pipeline(n_factors=3)

    result = pipeline.run(
        symbols=_SYMBOLS,
        start=dt.date(2026, 1, 1),
        end=dt.date(2026, 2, 28),
        frequency="daily",
        strategy=_NoopStrategy(),
        model=model,
        model_input_builder=lambda feats: build_persistent_panel(feats),
    )

    assert isinstance(result, ResearchPipelineResult)
    assert result.backtest_result is not None
    assert result.model_outputs is not None
    # The latent-factor prediction carries an asset-level forecast.
    assert hasattr(result.model_outputs, "asset_forecast")

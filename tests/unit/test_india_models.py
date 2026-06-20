"""Tests for :mod:`ml4t.india.models`.

Most tests use stub models patched into ``sys.modules`` so we don't
hit the upstream torch path during unit tests. The labelers don't
depend on ``ml4t.models``, so they're tested directly against polars.
"""

from __future__ import annotations

import datetime as dt
import sys
from types import SimpleNamespace
from typing import Any

import polars as pl
import pytest

from ml4t.india.core.exceptions import InvalidInputError

# ---- helpers -----------------------------------------------------------


def _install_fake_ml4t_models(monkeypatch: pytest.MonkeyPatch) -> SimpleNamespace:
    """Patch ``ml4t.models`` with a recording stub.

    Returns a :class:`SimpleNamespace` whose attributes are the stub
    classes. Tests can assert on which constructors were called and
    with what arguments by reading ``stub.<Class>.calls``.
    """

    class _Recorder:
        """Records constructor calls; emits a no-op instance."""

        def __init__(self, name: str) -> None:
            self.name = name
            self.calls: list[tuple[tuple[Any, ...], dict[str, Any]]] = []

        def __call__(self, *args: Any, **kwargs: Any) -> Any:
            self.calls.append((args, kwargs))
            return SimpleNamespace(_name=self.name, args=args, kwargs=kwargs)

    pca = _Recorder("PCAModel")
    rppca = _Recorder("RPPCAModel")
    ipca = _Recorder("IPCAModel")
    forecaster = _Recorder("ExpandingMeanFactorForecaster")
    mapper = _Recorder("BetaLambdaMapper")
    pipeline = _Recorder("LatentFactorForecastPipeline")
    portfolio = _Recorder("PortfolioAllocationPipeline")
    feed_inputs = _Recorder("BacktestDataFeedInputs")

    # Upstream constructs each model from a typed config dataclass. The
    # india wrappers pass ``XConfig(...)`` into ``XModel(config)``, so the
    # stub models record the *config* object as their single positional
    # arg. We mirror the real config classes with lightweight stand-ins
    # that capture the kwargs they were built with.
    class _Config:
        def __init__(self, **kwargs: Any) -> None:
            self.kwargs = kwargs

    fake = SimpleNamespace(
        PCAModel=pca,
        PCAConfig=_Config,
        RPPCAModel=rppca,
        RPPCAConfig=_Config,
        IPCAModel=ipca,
        IPCAConfig=_Config,
        ExpandingMeanFactorForecaster=forecaster,
        BetaLambdaMapper=mapper,
        LatentFactorForecastPipeline=pipeline,
        PortfolioAllocationPipeline=portfolio,
    )
    fake_integration = SimpleNamespace(BacktestDataFeedInputs=feed_inputs)

    monkeypatch.setitem(sys.modules, "ml4t.models", fake)
    monkeypatch.setitem(sys.modules, "ml4t.models.integration", fake_integration)
    return SimpleNamespace(
        models=fake,
        integration=fake_integration,
        config_cls=_Config,
        pca=pca,
        rppca=rppca,
        ipca=ipca,
        forecaster=forecaster,
        mapper=mapper,
        pipeline=pipeline,
        portfolio=portfolio,
        feed_inputs=feed_inputs,
    )


# ---- factors -----------------------------------------------------------


class TestNseFactorPresets:
    @staticmethod
    def _config_kwargs(recorder: Any) -> dict[str, Any]:
        """Return the kwargs of the config the stub model was built with.

        The india wrappers call ``XModel(XConfig(**kwargs))``, so the
        recorded model is constructed with a single positional config
        argument exposing those kwargs.
        """
        args, _ = recorder.calls[0]
        return args[0].kwargs

    def test_pca_defaults(self, monkeypatch: pytest.MonkeyPatch) -> None:
        stub = _install_fake_ml4t_models(monkeypatch)
        from ml4t.india.models.factors import nse_pca_model

        nse_pca_model()
        assert stub.pca.calls
        kwargs = self._config_kwargs(stub.pca)
        assert kwargs["n_factors"] == 5

    def test_pca_overrides(self, monkeypatch: pytest.MonkeyPatch) -> None:
        stub = _install_fake_ml4t_models(monkeypatch)
        from ml4t.india.models.factors import nse_pca_model

        nse_pca_model(n_factors=3, seed=7)
        kwargs = self._config_kwargs(stub.pca)
        assert kwargs["n_factors"] == 3
        assert kwargs["seed"] == 7

    def test_rppca_defaults(self, monkeypatch: pytest.MonkeyPatch) -> None:
        stub = _install_fake_ml4t_models(monkeypatch)
        from ml4t.india.models.factors import nse_rppca_model

        nse_rppca_model()
        kwargs = self._config_kwargs(stub.rppca)
        assert kwargs["n_factors"] == 5
        # risk_premium_weight maps to the upstream RPPCAConfig.gamma field.
        assert kwargs["gamma"] == 10.0

    def test_ipca_factor_count(self, monkeypatch: pytest.MonkeyPatch) -> None:
        stub = _install_fake_ml4t_models(monkeypatch)
        from ml4t.india.models.factors import nse_ipca_model

        nse_ipca_model(n_factors=4)
        kwargs = self._config_kwargs(stub.ipca)
        assert kwargs["n_factors"] == 4


# ---- pipelines ---------------------------------------------------------


class TestNsePipelinePresets:
    def test_latent_factor_pipeline_default(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        stub = _install_fake_ml4t_models(monkeypatch)
        from ml4t.india.models.pipelines import nse_latent_factor_pipeline

        nse_latent_factor_pipeline()
        # pca should be called for the default model.
        assert stub.pca.calls
        # forecaster should be called for the default forecaster (the
        # upstream expanding-mean estimator no longer takes a window).
        assert stub.forecaster.calls
        # mapper should be called for the default asset mapper.
        assert stub.mapper.calls
        # pipeline assembled with model, forecaster and mapper.
        assert stub.pipeline.calls
        pipeline_kwargs = stub.pipeline.calls[0][1]
        assert {"model", "forecaster", "mapper"} <= pipeline_kwargs.keys()

    def test_portfolio_pipeline_requires_model(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _install_fake_ml4t_models(monkeypatch)
        from ml4t.india.models.pipelines import nse_portfolio_allocation_pipeline

        with pytest.raises(ValueError, match="requires a portfolio_model"):
            nse_portfolio_allocation_pipeline()


# ---- registry ----------------------------------------------------------


class TestRegistry:
    def test_built_in_presets_present(self) -> None:
        from ml4t.india.models.registry import list_presets

        names = list_presets()
        assert "nse_cash_long_only" in names
        assert "nse_fno_delta_neutral" in names
        assert "nse_sector_rotation" in names

    def test_resolve_existing_preset(self) -> None:
        from ml4t.india.models.registry import resolve_preset

        preset = resolve_preset("nse_cash_long_only")
        assert preset.name == "nse_cash_long_only"
        assert preset.metadata["universe"] == "NIFTY_100"

    def test_resolve_unknown_raises(self) -> None:
        from ml4t.india.models.registry import resolve_preset

        with pytest.raises(InvalidInputError, match="no preset named"):
            resolve_preset("nse_does_not_exist")

    def test_register_then_resolve(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # We mutate the module-level registry; restore after.
        from ml4t.india.models import registry as reg
        from ml4t.india.models.registry import (
            IndiaModelPreset,
            register_preset,
            resolve_preset,
        )

        before = dict(reg._REGISTRY)
        try:
            preset = IndiaModelPreset(
                name="test_preset_xyz",
                description="Test only.",
                pipeline_factory=lambda: object(),
                feed_inputs_factory=lambda: object(),
            )
            register_preset(preset)
            assert resolve_preset("test_preset_xyz") is preset
        finally:
            reg._REGISTRY.clear()
            reg._REGISTRY.update(before)

    def test_register_duplicate_raises(self) -> None:
        from ml4t.india.models.registry import (
            IndiaModelPreset,
            register_preset,
        )

        existing = IndiaModelPreset(
            name="nse_cash_long_only",
            description="duplicate",
            pipeline_factory=lambda: object(),
            feed_inputs_factory=lambda: object(),
        )
        with pytest.raises(InvalidInputError, match="already registered"):
            register_preset(existing)

    def test_pipeline_factory_calls_upstream(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        stub = _install_fake_ml4t_models(monkeypatch)
        from ml4t.india.models.registry import resolve_preset

        preset = resolve_preset("nse_cash_long_only")
        preset.pipeline_factory()  # forces the lazy import + construction
        assert stub.pipeline.calls


# ---- labelers ----------------------------------------------------------


class TestLabelers:
    def test_lot_size_normalized(self) -> None:
        from ml4t.india.models.labels import LotSizeNormalizedLabeler

        bars = pl.DataFrame(
            {
                "date": [
                    dt.date(2026, 4, 1),
                    dt.date(2026, 4, 2),
                    dt.date(2026, 4, 3),
                    dt.date(2026, 4, 4),
                    dt.date(2026, 4, 5),
                    dt.date(2026, 4, 6),
                ],
                "asset": ["NIFTY"] * 3 + ["BANKNIFTY"] * 3,
                "close": [25000.0, 25100.0, 25200.0, 50000.0, 50500.0, 51000.0],
            }
        )
        labeler = LotSizeNormalizedLabeler(
            horizon_days=2,
            lot_sizes={"NIFTY": 50, "BANKNIFTY": 15},
        )
        out = labeler.label(bars)
        # Both assets should produce one row each (after horizon=2 forward-shift).
        nifty_label = out.filter(pl.col("asset") == "NIFTY")["label"].to_list()
        bn_label = out.filter(pl.col("asset") == "BANKNIFTY")["label"].to_list()
        assert nifty_label[0] == pytest.approx((25200 / 25000 - 1) / 50)
        assert bn_label[0] == pytest.approx((51000 / 50000 - 1) / 15)

    def test_lot_size_invalid_horizon(self) -> None:
        from ml4t.india.models.labels import LotSizeNormalizedLabeler

        with pytest.raises(InvalidInputError, match="horizon_days"):
            LotSizeNormalizedLabeler(horizon_days=0)

    def test_lot_size_missing_columns(self) -> None:
        from ml4t.india.models.labels import LotSizeNormalizedLabeler

        bad = pl.DataFrame({"foo": [1, 2, 3]})
        labeler = LotSizeNormalizedLabeler()
        with pytest.raises(InvalidInputError, match="missing columns"):
            labeler.label(bad)

    def test_expiry_rolled_basic(self) -> None:
        from ml4t.india.models.labels import ExpiryRolledFuturesLabeler

        bars = pl.DataFrame(
            {
                "date": [
                    dt.date(2026, 4, 1),
                    dt.date(2026, 4, 2),
                    dt.date(2026, 4, 3),
                ],
                "asset": ["NIFTY26APRFUT"] * 3,
                "close": [25000.0, 25100.0, 25200.0],
            }
        )
        labeler = ExpiryRolledFuturesLabeler(
            horizon_days=1, use_settlement_price=False
        )
        out = labeler.label(bars, expiry_dates=[])
        # One forward-return per non-tail bar; horizon=1 leaves one drop-null row.
        assert len(out) == 2
        assert out["label"][0] == pytest.approx(25100 / 25000 - 1)

    def test_expiry_rolled_invalid_horizon(self) -> None:
        from ml4t.india.models.labels import ExpiryRolledFuturesLabeler

        with pytest.raises(InvalidInputError, match="horizon_days"):
            ExpiryRolledFuturesLabeler(horizon_days=0)

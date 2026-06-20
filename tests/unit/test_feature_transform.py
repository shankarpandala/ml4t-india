"""Creds-free unit tests for the stage-4 feature transform.

``examples/end_to_end.py`` lives outside the installable package, so we import
it by path (mirroring ``tests/integration/test_end_to_end.py``).

Regression guard for a confirmed silent-failure bug: the transform used to call
``compute_features(group, ["returns", "sma_10", "sma_20", "rsi_14",
"volatility_20"])`` -- none of which are real ``ml4t.engineer`` registry names,
so ``compute_features`` raised ``ValueError: Feature 'returns' not found in
registry``. A bare ``except Exception: enriched = group`` swallowed it and fell
back to raw OHLCV, so Stage 4 *claimed* features were wired while computing
nothing. These tests assert the transform now (a) produces the real feature
columns from the real registry, and (b) fails loudly on a bad registry name
instead of silently degrading.
"""

from __future__ import annotations

import importlib.util
from datetime import datetime, timedelta
from pathlib import Path

import numpy as np
import polars as pl
import pytest


def _load_orchestrator():
    repo_root = Path(__file__).resolve().parents[2]
    script = repo_root / "examples" / "end_to_end.py"
    spec = importlib.util.spec_from_file_location("ml4t_india_e2e", script)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_E2E = _load_orchestrator()


def _ohlcv(symbols=("INFY", "TCS"), n: int = 40) -> pl.DataFrame:
    """Small synthetic multi-symbol OHLCV frame (library-test scaffolding)."""
    rng = np.random.default_rng(7)
    base = datetime(2026, 1, 1)
    frames = []
    for sym in symbols:
        close = 100.0 + np.cumsum(rng.standard_normal(n))
        frames.append(
            pl.DataFrame(
                {
                    "timestamp": [base + timedelta(days=i) for i in range(n)],
                    "symbol": sym,
                    "open": close,
                    "high": close + 1.0,
                    "low": close - 1.0,
                    "close": close,
                    "volume": rng.uniform(1e3, 1e4, n),
                }
            )
        )
    return pl.concat(frames, how="vertical_relaxed")


def test_feature_transform_produces_real_feature_columns() -> None:
    """The transform must add every declared output column with real values."""
    out = _E2E._feature_transform(_ohlcv())

    for col in _E2E._FEATURES:
        assert col in out.columns, f"missing produced feature column {col!r}"

    # The registry-backed characteristic columns are exactly _PANEL_FEATURES.
    assert _E2E._PANEL_FEATURES == ["sma_10", "sma_20", "rsi_14", "volatility_20"]
    for col in _E2E._PANEL_FEATURES:
        assert col in out.columns

    # Real (not all-null) values once the windows warm up. SMA(20) needs 20
    # bars; with 40 bars per symbol the tail must be finite and non-null.
    tail = out.sort(["symbol", "timestamp"]).group_by("symbol").tail(1)
    for col in ("sma_10", "sma_20", "rsi_14", "volatility_20"):
        vals = tail[col].to_list()
        assert all(v is not None for v in vals), f"{col} still null at tail"


def test_feature_transform_sma_windows_are_distinct() -> None:
    """sma_10 and sma_20 are genuinely different windows, not one colliding column."""
    out = _E2E._feature_transform(_ohlcv(symbols=("INFY",)))
    tail = out.sort("timestamp").tail(1)
    sma10 = tail["sma_10"].to_list()[0]
    sma20 = tail["sma_20"].to_list()[0]
    assert sma10 is not None and sma20 is not None
    # Different lookbacks over a random walk must not coincide.
    assert sma10 != pytest.approx(sma20)


def test_feature_transform_returns_is_close_to_close() -> None:
    """The ``returns`` column is the simple close-to-close return per symbol."""
    out = _E2E._feature_transform(_ohlcv(symbols=("INFY",), n=5)).sort("timestamp")
    closes = out["close"].to_list()
    returns = out["returns"].to_list()
    assert returns[0] is None  # first bar has no prior close
    assert returns[1] == pytest.approx(closes[1] / closes[0] - 1.0)


def test_feature_transform_fails_loud_on_bad_registry_name() -> None:
    """A bad registry feature name must raise, not silently fall back to OHLCV.

    This is the core regression: the old bare ``except Exception`` hid exactly
    this ``ValueError``. We monkeypatch the spec to inject a non-existent
    feature and assert the error propagates.
    """
    original = _E2E._REGISTRY_SPECS
    _E2E._REGISTRY_SPECS = (("bogus_feature", "definitely_not_a_real_feature", {}),)
    try:
        with pytest.raises(ValueError, match="not found in registry"):
            _E2E._feature_transform(_ohlcv(symbols=("INFY",)))
    finally:
        _E2E._REGISTRY_SPECS = original


def test_feature_transform_feeds_panel_with_characteristics() -> None:
    """Real feature columns flow into the panel as a (T, N, F) characteristics tensor."""
    out = _E2E._feature_transform(_ohlcv(symbols=("INFY", "TCS", "HDFCBANK")))
    batch = _E2E._build_panel(out)

    assert batch.characteristics is not None
    # (T, N, F): F == number of real characteristic columns produced.
    assert batch.characteristics.shape[2] == len(_E2E._PANEL_FEATURES)
    assert batch.returns.shape[1] == 3  # three symbols

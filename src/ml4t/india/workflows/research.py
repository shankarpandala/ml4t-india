"""Research pipeline: data -> features -> backtest.

A thin orchestrator that chains the three stages every quant strategy
goes through in ml4t-land:

1. Pull historical OHLCV from an :class:`IndianOHLCVProvider`
   (typically :class:`KiteProvider`).
2. Optionally apply a feature-engineering callable -- downstream
   usually :mod:`ml4t.engineer` built-ins.
3. Run the strategy through :class:`ml4t.backtest.Engine` with an
   India-flavored :class:`BacktestConfig` (see
   :func:`~ml4t.india.backtest.nse_india_config`).

The point of this class is NOT to re-implement Engine; it's to make
the correct-for-India wiring the one-liner.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

import polars as pl

from ml4t.india.backtest import nse_india_config
from ml4t.india.data.base import IndianOHLCVProvider

FeatureTransform = Callable[[pl.DataFrame], pl.DataFrame]


@dataclass
class ResearchPipelineResult:
    """Bundle of stage outputs so callers can inspect each layer.

    ``data`` is the raw OHLCV frame, ``features`` is the transformed
    frame (identical to ``data`` when no transform was supplied),
    ``model_outputs`` is whatever the optional model returned (``None``
    when no model was supplied), and ``backtest_result`` is whatever
    the engine returned -- we pass model_outputs and backtest_result
    through as :class:`Any` to stay decoupled from upstream's result
    class hierarchy.
    """

    data: pl.DataFrame
    features: pl.DataFrame
    backtest_result: Any
    model_outputs: Any = None


class ResearchPipeline:
    """Compose provider + feature transform + backtest engine.

    Parameters
    ----------
    provider:
        Any :class:`IndianOHLCVProvider`. Typically
        :class:`~ml4t.india.data.KiteProvider`, but tests can pass a
        fake that returns canned frames.
    feature_transform:
        Optional callable that takes the raw OHLCV frame and returns a
        feature-augmented frame. ``None`` means pass raw data straight
        to the backtest.
    config_overrides:
        Keyword overrides forwarded to
        :func:`~ml4t.india.backtest.nse_india_config`. Lets a caller
        bump commission / initial_cash / etc. without rebuilding the
        whole preset.

    Notes
    -----
    The backtest is lazily imported inside :meth:`run`. Upstream
    :mod:`ml4t.backtest` is a moderately heavy import (brings in
    pandas, sklearn, etc.), so deferring keeps ``import
    ml4t.india.workflows`` cheap for callers that only wire the
    deployment pipeline.
    """

    def __init__(
        self,
        provider: IndianOHLCVProvider,
        feature_transform: FeatureTransform | None = None,
        **config_overrides: Any,
    ) -> None:
        self._provider = provider
        self._feature_transform = feature_transform
        self._config_overrides = dict(config_overrides)

    def run(
        self,
        symbols: list[str],
        start: dt.date | dt.datetime,
        end: dt.date | dt.datetime,
        frequency: str,
        strategy: Any,
        model: Any | None = None,
    ) -> ResearchPipelineResult:
        """Execute data -> features -> [model] -> backtest.

        Parameters
        ----------
        symbols, start, end, frequency:
            Forwarded to ``provider.fetch_ohlcv``.
        strategy:
            An :class:`ml4t.backtest.Strategy` subclass instance. We do
            not validate the shape here -- that's upstream's job; any
            instance that the backtest engine accepts works.
        model:
            Optional :mod:`ml4t.models` model or pipeline. When supplied,
            the pipeline fits it on the feature stage output and converts
            its predictions / weights into ``BacktestDataFeedInputs``
            that ride the engine alongside the strategy. Use the presets
            in :mod:`ml4t.india.models.registry` to skip the assembly.
            ``None`` (default) keeps the legacy data-only flow.

        Returns
        -------
        ResearchPipelineResult
            ``data`` / ``features`` / ``model_outputs`` / ``backtest_result`` bundle.
        """
        # Stage 1: fetch.
        data = self._provider.fetch_ohlcv(
            symbols=symbols,
            start=start,
            end=end,
            frequency=frequency,
        )

        # Stage 2: features (optional).
        features = self._feature_transform(data) if self._feature_transform else data

        # Stage 3: model (optional).
        model_outputs: Any = None
        if model is not None:
            # Both raw models and pipelines expose a `.fit_predict` or
            # equivalent. We try the pipeline interface first (`run`),
            # then fall back to fit + transform. Failing both, the
            # caller-supplied object isn't a recognised model surface
            # and we let the AttributeError bubble up.
            if hasattr(model, "run"):
                model_outputs = model.run(features)
            elif hasattr(model, "fit_predict"):
                model_outputs = model.fit_predict(features)
            else:
                model.fit(features)
                model_outputs = (
                    model.predict(features) if hasattr(model, "predict") else None
                )

        # Stage 4: backtest.
        from ml4t.backtest import DataFeed, Engine  # local import, see class docstring

        config = nse_india_config(**self._config_overrides)
        # Engine needs a DataFeed, not a raw OHLCV frame. ``features`` already
        # carries the provider's timestamp/symbol/OHLCV schema, which DataFeed
        # auto-resolves; this is a generic research backtest with no separate
        # signals frame, so ``signals_df`` is left unset (the strategy emits
        # its own orders via ``on_data``).
        feed = DataFeed(prices_df=features)
        engine = Engine(feed=feed, strategy=strategy, config=config)
        backtest_result = engine.run()

        return ResearchPipelineResult(
            data=data,
            features=features,
            model_outputs=model_outputs,
            backtest_result=backtest_result,
        )


__all__ = ["FeatureTransform", "ResearchPipeline", "ResearchPipelineResult"]

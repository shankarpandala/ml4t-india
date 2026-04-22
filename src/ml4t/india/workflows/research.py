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
    frame (identical to ``data`` when no transform was supplied), and
    ``backtest_result`` is whatever the engine returned -- we pass it
    through as :class:`Any` to stay decoupled from upstream's result
    class hierarchy.
    """

    data: pl.DataFrame
    features: pl.DataFrame
    backtest_result: Any


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
    ) -> ResearchPipelineResult:
        """Execute data -> features -> backtest.

        Parameters
        ----------
        symbols, start, end, frequency:
            Forwarded to ``provider.fetch_ohlcv``.
        strategy:
            An :class:`ml4t.backtest.Strategy` subclass instance. We do
            not validate the shape here -- that's upstream's job; any
            instance that the backtest engine accepts works.

        Returns
        -------
        ResearchPipelineResult
            ``data`` / ``features`` / ``backtest_result`` bundle.
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

        # Stage 3: backtest.
        from ml4t.backtest import Engine  # local import, see class docstring

        config = nse_india_config(**self._config_overrides)
        engine = Engine(config=config)
        backtest_result = engine.run(strategy=strategy, data=features)

        return ResearchPipelineResult(
            data=data,
            features=features,
            backtest_result=backtest_result,
        )


__all__ = ["FeatureTransform", "ResearchPipeline", "ResearchPipelineResult"]

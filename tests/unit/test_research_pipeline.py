"""Regression tests for :class:`ml4t.india.workflows.research.ResearchPipeline`.

The pipeline's ``run`` must hand the backtest :class:`ml4t.backtest.Engine`
a :class:`~ml4t.backtest.DataFeed`, NOT a raw OHLCV ``pl.DataFrame``. Engine
construction does not type-check its ``feed`` argument, so passing a frame
constructs fine but crashes at runtime inside ``Engine.run()`` (which reads
``self.feed.timestamps`` and iterates the feed). Nothing else exercises
``run`` end-to-end, so this test guards that wiring directly: it FAILS against
the pre-fix DataFrame-as-feed code and PASSES once ``features`` is wrapped in
a ``DataFeed``.
"""

from __future__ import annotations

import datetime as dt
from typing import Any

import polars as pl
from ml4t.backtest import Strategy

from ml4t.india.data.base import IndianOHLCVProvider
from ml4t.india.workflows.research import ResearchPipeline, ResearchPipelineResult


def _synthetic_ohlcv(symbols: list[str], bars: int = 30) -> pl.DataFrame:
    """Tiny minute-bar OHLCV frame in the provider's canonical schema."""
    rows = []
    start = dt.datetime(2026, 1, 5, 9, 15)
    for symbol in symbols:
        price = 100.0
        for minute in range(bars):
            price += 0.1
            rows.append(
                {
                    "timestamp": start + dt.timedelta(minutes=minute),
                    "symbol": symbol,
                    "open": price,
                    "high": price + 0.5,
                    "low": price - 0.5,
                    "close": price,
                    "volume": 100.0,
                }
            )
    return pl.DataFrame(rows)


class _FakeProvider(IndianOHLCVProvider):
    """OHLCV provider returning canned synthetic bars."""

    @property
    def name(self) -> str:
        return "fake"

    def fetch_ohlcv(  # type: ignore[override]  # research.py calls with plural symbols=
        self,
        symbols: list[str],
        start: Any,
        end: Any,
        frequency: str,
    ) -> pl.DataFrame:
        return _synthetic_ohlcv(symbols)


class _NoopStrategy(Strategy):
    """Strategy that never trades -- still drives full feed iteration."""

    def on_data(self, timestamp, data, context, broker) -> None:  # noqa: ANN001
        return None


def test_research_pipeline_run_wraps_features_in_datafeed() -> None:
    """run() executes end-to-end and returns a populated result.

    A no-op strategy is enough: ``Engine.run`` still reads
    ``feed.timestamps`` and iterates the feed into ``(ts, assets, context)``
    triples -- the exact operations that raise ``AttributeError`` when a bare
    DataFrame is passed as the feed.
    """
    pipeline = ResearchPipeline(provider=_FakeProvider())

    result = pipeline.run(
        symbols=["AAA"],
        start=dt.date(2026, 1, 5),
        end=dt.date(2026, 1, 6),
        frequency="minute",
        strategy=_NoopStrategy(),
    )

    assert isinstance(result, ResearchPipelineResult)
    assert result.backtest_result is not None

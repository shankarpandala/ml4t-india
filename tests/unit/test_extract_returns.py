"""Creds-free unit tests for the stage-8 returns extractor.

``examples/end_to_end.py`` lives outside the installable package, so we import
it by path (mirroring ``tests/integration/test_end_to_end.py``). These tests
pin the real ``ml4t.backtest.BacktestResult`` shape: ``equity_curve`` is a list
of ``(timestamp, portfolio_value)`` tuples, so iterating it yields tuples. Before
the fix, ``_extract_returns`` did ``float(v)`` on each tuple and raised
``TypeError: float() argument must be a string or a real number, not 'tuple'``.
"""

from __future__ import annotations

import importlib.util
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

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


def _features() -> pl.DataFrame:
    base = datetime(2026, 1, 1)
    return pl.DataFrame(
        {
            "symbol": ["INFY"] * 5,
            "timestamp": [base + timedelta(days=i) for i in range(5)],
            "close": [100.0, 101.0, 102.0, 101.5, 103.0],
        }
    )


def _equity_curve_tuples() -> list[tuple[datetime, float]]:
    base = datetime(2026, 1, 1)
    values = [100_000.0, 101_000.0, 100_500.0, 102_000.0, 103_500.0]
    return [(base + timedelta(days=i), v) for i, v in enumerate(values)]


def test_extract_returns_from_tuple_equity_curve() -> None:
    """The real (timestamp, value) tuple shape must not raise and must yield returns."""
    result = SimpleNamespace(equity_curve=_equity_curve_tuples())
    returns = _E2E._extract_returns(result, _features())

    assert isinstance(returns, list)
    assert len(returns) == 4  # one fewer than the 5 equity points
    assert all(isinstance(r, float) for r in returns)
    # First step: 101000/100000 - 1 = 0.01
    assert returns[0] == pytest.approx(0.01)


def test_extract_returns_from_named_rows() -> None:
    """A polars-Row / namedtuple shape with a named ``equity`` field also works."""
    base = datetime(2026, 1, 1)
    rows = [
        SimpleNamespace(timestamp=base + timedelta(days=i), equity=v)
        for i, v in enumerate([10.0, 11.0, 12.0, 13.0])
    ]
    result = SimpleNamespace(equity_curve=rows)
    returns = _E2E._extract_returns(result, _features())

    assert len(returns) == 3
    assert returns[0] == pytest.approx(0.1)


def test_extract_returns_falls_back_to_close_prices() -> None:
    """Empty/absent curve falls back to close-to-close returns of the first symbol."""
    result = SimpleNamespace(equity_curve=[])
    returns = _E2E._extract_returns(result, _features())

    # 4 close-to-close returns from the 5 close prices.
    assert len(returns) == 4
    assert returns[0] == pytest.approx(0.01)  # 101/100 - 1


def test_coerce_equity_value_variants() -> None:
    coerce = _E2E._coerce_equity_value
    assert coerce(5.0) == 5.0
    assert coerce((datetime(2026, 1, 1), 7.5)) == 7.5
    assert coerce(SimpleNamespace(equity=3.0)) == 3.0
    assert coerce({"value": 2.0}) == 2.0
    assert coerce(True) is None  # bool is not a portfolio value
    assert coerce(("no", "numbers")) is None

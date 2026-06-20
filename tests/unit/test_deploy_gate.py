"""Creds-free unit tests for the stage-8 deploy gate's fail-soft behaviour.

``examples/end_to_end.py`` lives outside the installable package, so we import
it by path (mirroring ``tests/unit/test_extract_returns.py``).

The gate must fail SOFT on a degenerate return series: while Stage 6 runs the
signal-free placeholder strategy the backtest makes no trades, so the extracted
returns are all-zero (zero variance). The upstream ``deflated_sharpe_ratio``
legitimately refuses that with ``ValueError("Return series has zero
variance")``. Before the fix, ``_deploy_gate`` called the DSR unconditionally
and that ValueError crashed the whole end-to-end run at stage 8. After the fix
the gate short-circuits to an honest NOT-READY verdict without raising, while a
non-degenerate series still exercises the real DSR path.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path
from types import SimpleNamespace

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


@pytest.fixture
def dsr_spy(monkeypatch):
    """Spy on the upstream ``deflated_sharpe_ratio`` to record whether the real
    gate path is taken, and force a significant result so READY is deterministic.
    """
    import ml4t.diagnostic.evaluation.stats as stats

    calls: list[list[float]] = []

    def _fake(returns, frequency="daily"):  # noqa: ARG001
        calls.append(list(returns))
        return SimpleNamespace(
            sharpe_ratio_annualized=2.5, probability=0.99, is_significant=True
        )

    monkeypatch.setattr(stats, "deflated_sharpe_ratio", _fake)
    return calls


@pytest.mark.parametrize(
    "returns",
    [
        [0.0, 0.0, 0.0, 0.0, 0.0],  # all-zero: the real placeholder-strategy case
        [0.001] * 6,  # all-equal non-zero: still zero variance
        [],  # empty
        [0.01],  # a single point: too few to gate on
    ],
)
def test_deploy_gate_failsoft_on_degenerate_series(returns, dsr_spy):
    """A degenerate series returns NOT-READY (False) WITHOUT raising, and never
    reaches the DSR."""
    ready = _E2E._deploy_gate(returns)

    assert ready is False
    assert dsr_spy == []  # short-circuited before the deflated Sharpe call


def test_deploy_gate_computes_real_gate_on_nondegenerate_series(dsr_spy, monkeypatch):
    """A non-degenerate series flows through to the real DSR and gates on it."""
    # Isolate the gate from the optional agent extra: force the agent leg to pass
    # so a significant DSR yields a deterministic READY verdict.
    monkeypatch.setattr(_E2E, "_run_agent", lambda **_: True)

    returns = [0.01, -0.005, 0.02, -0.01, 0.015, 0.0, -0.02, 0.03]
    ready = _E2E._deploy_gate(returns)

    assert ready is True
    assert len(dsr_spy) == 1  # the real deflated-Sharpe path WAS exercised
    assert dsr_spy[0] == returns


def test_deploy_gate_catches_late_zero_variance_valueerror(monkeypatch):
    """Defensive backstop: a ValueError from the stats layer degrades to
    NOT-READY rather than crashing."""
    import ml4t.diagnostic.evaluation.stats as stats

    def _boom(returns, frequency="daily"):  # noqa: ARG001
        raise ValueError("Return series has zero variance")

    monkeypatch.setattr(stats, "deflated_sharpe_ratio", _boom)

    # A series that passes the up-front degeneracy check but trips the stats layer.
    ready = _E2E._deploy_gate([0.01, -0.005, 0.02, -0.01, 0.015])

    assert ready is False

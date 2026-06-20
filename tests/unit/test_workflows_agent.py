"""Tests for IndiaResearchAgent -- ml4t-agent wired with India defaults.

These exercise the wrapper end-to-end in Mock mode against a tiny on-disk
``run_dir`` (a minimal ``evidence_pack.json``). They never touch the
network or an API key: the LLM is a keyless upstream ``MockLLMClient`` with
a deterministic responder.

The whole module is skipped when the optional ``agent`` extra (ml4t-agent)
is not installed, so a mock-only environment (e.g. the conda build) still
collects cleanly.
"""

from __future__ import annotations

import dataclasses
import enum
import json
from pathlib import Path
from typing import Any

import pytest

pytest.importorskip("ml4t.agent")

from ml4t.agent import MockLLMClient  # noqa: E402
from ml4t.agent.llm import LLMResponse  # noqa: E402
from ml4t.agent.schemas import (  # noqa: E402
    BacktestSummary,
    CostSummary,
    Determinism,
    EvidencePack,
    LineageSummary,
    MethodologyWarning,
    ModelRunSummary,
    RobustnessSummary,
    Severity,
    SignalSummary,
    ValidationDesign,
    ValidationMetrics,
)

from ml4t.india.core.universe import PRESETS  # noqa: E402
from ml4t.india.workflows import IndiaResearchAgent  # noqa: E402


class _EnumEncoder(json.JSONEncoder):
    """Serialize ml4t-agent's str-enums to their wire values."""

    def default(self, o: Any) -> Any:
        if isinstance(o, enum.Enum):
            return o.value
        return super().default(o)


def _build_pack(warning: MethodologyWarning) -> EvidencePack:
    """A minimal-but-valid evidence pack for an India momentum line."""
    return EvidencePack(
        case_study="india-momentum",
        objective="Does cross-sectional momentum survive NSE costs?",
        primary_label="fwd_20d_return",
        horizon="20d",
        cadence="daily",
        parent_run_id="run-0001",
        line_id="india-research",
        validation_design=ValidationDesign(
            n_splits=5,
            train_size="756d",
            val_size="252d",
            holdout_start="2025-01-01",
            holdout_end="2025-06-30",
            embargo_days=5,
            purge_days=5,
            fold_calendar_id="nse-2024",
        ),
        validation=ValidationMetrics(
            model_runs=(
                ModelRunSummary(
                    family="linear",
                    config_name="ridge_a",
                    label="fwd_20d_return",
                    mean_fold_ic=0.03,
                    fold_ic_std=0.01,
                    fold_sharpe_mean=0.6,
                    fold_sharpe_std=0.2,
                    n_folds=5,
                ),
            ),
            signal=SignalSummary(
                best_family="linear",
                best_config="ridge_a",
                best_mean_ic=0.03,
                ic_t_stat=1.4,
                horizons_tested=("20d",),
            ),
            backtests=(
                BacktestSummary(
                    config_id="bt-1",
                    family="linear",
                    gross_sharpe=0.9,
                    net_sharpe=0.4,
                    max_drawdown=-0.2,
                    mean_monthly_turnover=1.5,
                    edge_to_cost_ratio=1.2,
                ),
            ),
            robustness=RobustnessSummary(
                fold_sharpe_dispersion=0.3,
                regime_breakdown_available=False,
                seed_variance_estimate=0.05,
            ),
        ),
        holdout=None,
        costs=CostSummary(
            per_leg_bps_low=3.0,
            per_leg_bps_high=8.0,
            cost_class="medium",
            components=("brokerage", "stt", "slippage"),
        ),
        lineage=LineageSummary(
            git_sha="abc123",
            data_snapshot_id="snap-1",
            config_hash="cfg-1",
            package_versions={"ml4t-agent": "0.8.0a1"},
        ),
        warnings=(warning,),
        diagnostics=(),
        triage=None,
        preset_registry=None,
    )


def _write_evidence_pack(run_dir: Path, warning: MethodologyWarning) -> None:
    payload = json.dumps(dataclasses.asdict(_build_pack(warning)), cls=_EnumEncoder)
    (run_dir / "evidence_pack.json").write_text(payload, encoding="utf-8")


# A plain WARNING keeps validity "clean" (cost templates stay eligible).
_BENIGN_WARNING = MethodologyWarning(
    code="W001",
    message="single label tested",
    severity=Severity.WARNING,
    determinism=Determinism.RUNNER_CONTRACT,
    triggering_artifact="validation",
    triggering_field="primary_label",
)

# A MATERIAL warning that cites lineage makes the T-A2 (lineage recheck)
# template eligible, so a proposal survives to the research note.
_LINEAGE_WARNING = MethodologyWarning(
    code="W_LINEAGE_DRIFT",
    message="git sha changed mid-run",
    severity=Severity.MATERIAL,
    determinism=Determinism.RUNNER_CONTRACT,
    triggering_artifact="lineage",
    triggering_field="git_sha",
)


def _ta2_responder(messages: Any, json_schema: Any) -> LLMResponse:
    """Deterministic responder proposing one valid T-A2 experiment."""
    return LLMResponse(
        data={
            "proposals": [
                {
                    "experiment_id": "india-research-exp-1",
                    "template_id": "T-A2",
                    "comparison_target_id": "run-0001",
                    "hypothesis": "Edge reproduces after a lineage recheck.",
                    "rationale": "Re-run lineage recheck against the parent baseline.",
                    "config_patches": [],
                }
            ]
        },
        raw="{}",
        usage={},
    )


class TestIndiaResearchAgentConstruction:
    def test_resolves_universe_to_symbols(self) -> None:
        agent = IndiaResearchAgent(universe="nifty50")
        assert agent.universe == "nifty50"
        assert agent.symbols == PRESETS["nifty50"]
        assert agent.line_id == "india-research"
        assert agent.iteration_index == 1

    def test_unknown_universe_raises(self) -> None:
        with pytest.raises(ValueError, match="unknown universe preset"):
            IndiaResearchAgent(universe="does-not-exist")


class TestIndiaResearchAgentRun:
    def test_default_mock_run_returns_research_note(self, tmp_path: Path) -> None:
        """Default keyless agent runs end-to-end and returns a ResearchNote.

        The default empty-proposals responder yields no plans, so the loop
        finalises a note without needing rerun/delta fixtures.
        """
        _write_evidence_pack(tmp_path, _BENIGN_WARNING)

        agent = IndiaResearchAgent(line_id="india-research", iteration_index=1)
        note = agent.run(tmp_path)

        assert type(note).__name__ == "ResearchNote"
        assert note.line_id == "india-research"
        assert note.iteration_index == 1
        assert note.case_study == "india-momentum"
        assert isinstance(note.summary, str) and note.summary
        # Decision is an upstream enum; it must carry a non-empty value.
        assert note.decision.value

    def test_run_threads_line_id_and_iteration(self, tmp_path: Path) -> None:
        _write_evidence_pack(tmp_path, _BENIGN_WARNING)

        agent = IndiaResearchAgent(line_id="custom-line", iteration_index=3)
        note = agent.run(tmp_path)

        assert note.line_id == "custom-line"
        assert note.iteration_index == 3

    def test_run_surfaces_a_proposal(self, tmp_path: Path) -> None:
        """A real (mocked) proposal survives into the research note."""
        _write_evidence_pack(tmp_path, _LINEAGE_WARNING)
        llm = MockLLMClient(responder=_ta2_responder)

        agent = IndiaResearchAgent(llm=llm)
        note = agent.run(tmp_path)

        assert len(note.proposals) == 1
        assert note.proposals[0].template_id == "T-A2"
        assert note.selected_experiment_id == "india-research-exp-1"
        # The mock LLM was actually consulted (no network, no key).
        assert len(llm.calls) == 1

    def test_agent_overrides_forwarded(self, tmp_path: Path) -> None:
        """**agent_overrides reach the upstream ResearchReviewAgent."""
        _write_evidence_pack(tmp_path, _BENIGN_WARNING)

        agent = IndiaResearchAgent(agent_id="custom.v9", max_steps=4)
        note = agent.run(tmp_path)

        assert note.line_id == "india-research"

"""LLM research-review agent wired with Indian-market defaults.

A thin wrapper around upstream :class:`ml4t.agent.ResearchReviewAgent`
that mirrors the lazy-import + inject-India-defaults shape of
:mod:`ml4t.india.workflows.research`:

* upstream ``ml4t.agent`` is an **alpha**, opt-in dependency (the
  ``agent`` extra), so every reference to it is imported lazily inside a
  method body and guarded with a friendly install hint -- importing this
  module, or :mod:`ml4t.india.workflows`, never requires ml4t-agent.
* the agent is **artifact-driven**: it reads ``run_dir/evidence_pack.json``
  (and optional rerun / delta-review fixtures) and makes no market-data
  calls of its own. We therefore do not fabricate any data; we only thread
  India configuration -- the requested universe preset from
  :mod:`ml4t.india.core.universe` and a stable research ``line_id`` -- so a
  caller gets the correct-for-India wiring as a one-liner.

The default LLM is a keyless, deterministic :class:`MockLLMClient`, so an
``IndiaResearchAgent()`` is safe to construct and run with no API key. Pass
an explicit ``llm=`` (e.g. an Anthropic adapter) for real proposals.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from ml4t.india.core.universe import PRESETS

if TYPE_CHECKING:  # pragma: no cover - typing only, never imported at runtime
    from ml4t.agent import ResearchNote

_IMPORT_HINT = "ml4t-agent is required; install with: pip install ml4t-india[agent]"


def _default_mock_responder(messages: Any, json_schema: Any) -> Any:  # noqa: ARG001
    """Keyless, deterministic stand-in for a real LLM.

    Upstream's ``MockLLMClient()`` refuses to construct without a
    ``responder`` or recordings (it has no canned default), so we supply
    one that returns an empty ``proposals`` list. That keeps the default
    agent runnable end-to-end with no API key and no network: the proposal
    tool simply yields no plans and the agent finalises a research note.
    Callers who want real proposals pass an explicit ``llm=``.
    """
    from ml4t.agent.llm import LLMResponse  # noqa: PLC0415

    return LLMResponse(data={"proposals": []}, raw="{}", usage={})


class IndiaResearchAgent:
    """Run ml4t-agent's research-review loop with Indian-market defaults.

    Parameters
    ----------
    llm:
        Provider-agnostic LLM adapter (upstream ``LLMClient``). ``None``
        (default) builds a keyless :class:`MockLLMClient` with a
        deterministic empty-proposals responder -- safe to run offline.
        Pass an Anthropic / OpenAI adapter for real proposals.
    universe:
        Key into :data:`ml4t.india.core.universe.PRESETS` (``indices``,
        ``nifty50``, ``banknifty``, ``finnifty``, ``nifty_fnf``). Resolved
        to its tradingsymbol tuple and exposed as :attr:`symbols`; threaded
        as research metadata since the agent itself is artifact-driven.
    line_id:
        Stable identifier for the research line; flows into the upstream
        ``LineState`` and is echoed on every emitted ``ResearchNote``.
    iteration_index:
        1-based iteration counter for the line; sets the Bonferroni ``k``
        upstream applies to each proposed experiment.
    agent_overrides:
        Forwarded verbatim to :class:`ml4t.agent.ResearchReviewAgent`
        (e.g. ``templates``, ``max_steps``, ``agent_id``,
        ``propose_temperature``).

    Notes
    -----
    Every ml4t-agent symbol is imported lazily inside a method body and
    guarded with :data:`_IMPORT_HINT`; constructing or running the agent
    without the ``agent`` extra installed raises a friendly
    :class:`ImportError`.
    """

    def __init__(
        self,
        *,
        llm: Any | None = None,
        universe: str = "indices",
        line_id: str = "india-research",
        iteration_index: int = 1,
        **agent_overrides: Any,
    ) -> None:
        try:
            from ml4t.agent import MockLLMClient  # noqa: PLC0415
            from ml4t.agent.schemas.workflow import LineState  # noqa: PLC0415
        except ImportError as exc:  # pragma: no cover - exercised via monkeypatch
            raise ImportError(_IMPORT_HINT) from exc

        if universe not in PRESETS:
            valid = ", ".join(sorted(PRESETS))
            raise ValueError(
                f"unknown universe preset {universe!r}; valid presets: {valid}"
            )

        self.universe = universe
        self.symbols = PRESETS[universe]
        self.line_id = line_id
        self.iteration_index = iteration_index
        self._agent_overrides = dict(agent_overrides)

        self._llm = llm if llm is not None else MockLLMClient(
            responder=_default_mock_responder
        )
        self._line_state = LineState(line_id=line_id, iteration_index=iteration_index)

    def run(self, run_dir: Any) -> ResearchNote:
        """Execute one research-review iteration over ``run_dir``.

        ``run_dir`` must contain ``evidence_pack.json`` (and any rerun /
        delta-review fixtures the selected template needs). Returns the
        upstream :class:`ResearchNote` unchanged so callers stay decoupled
        from our wiring.
        """
        try:
            from ml4t.agent import ResearchReviewAgent  # noqa: PLC0415
        except ImportError as exc:  # pragma: no cover - exercised via monkeypatch
            raise ImportError(_IMPORT_HINT) from exc

        agent = ResearchReviewAgent(
            llm=self._llm,
            line_state=self._line_state,
            **self._agent_overrides,
        )
        return agent.run(run_dir)


__all__ = ["IndiaResearchAgent"]

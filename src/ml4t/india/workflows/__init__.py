""":mod:`ml4t.india.workflows` -- high-level Indian-market pipelines.

Orchestrators that compose the rest of ml4t-india + upstream:

* :class:`ResearchPipeline` -- data -> features -> backtest. A
  one-call path from "give me NIFTY-50 daily bars" to a
  :class:`~ml4t.backtest.BacktestResult` -- so notebooks stay short.
* :class:`DeploymentPipeline` -- live-trading wiring. Connects
  :class:`~ml4t.india.live.KiteBroker` + :class:`KiteTickerFeed` to
  a strategy and owns the start/stop lifecycle.
* :class:`IndiaResearchAgent` -- ml4t-agent's LLM research-review loop
  wired with Indian-market defaults. ml4t-agent is an opt-in alpha
  dependency (the ``agent`` extra), so the class lazily imports it inside
  method bodies; importing this package never requires ml4t-agent.

The orchestrators are thin by design: they do NOT introduce new domain
logic, only compose existing pieces with Indian-market defaults
(nse_india_config, BSE calendar, IST timezone, NSE index universes).
"""

from __future__ import annotations

from ml4t.india.workflows.agent import IndiaResearchAgent
from ml4t.india.workflows.deployment import DeploymentPipeline
from ml4t.india.workflows.research import ResearchPipeline

__all__ = [
    "DeploymentPipeline",
    "IndiaResearchAgent",
    "ResearchPipeline",
]

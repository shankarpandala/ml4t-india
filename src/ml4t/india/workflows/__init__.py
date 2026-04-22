""":mod:`ml4t.india.workflows` -- high-level Indian-market pipelines.

Two orchestrators that compose the rest of ml4t-india + upstream:

* :class:`ResearchPipeline` -- data -> features -> backtest. A
  one-call path from "give me NIFTY-50 daily bars" to a
  :class:`~ml4t.backtest.BacktestResult` -- so notebooks stay short.
* :class:`DeploymentPipeline` -- live-trading wiring. Connects
  :class:`~ml4t.india.live.KiteBroker` + :class:`KiteTickerFeed` to
  a strategy and owns the start/stop lifecycle.

Both orchestrators are thin by design: they do NOT introduce new
domain logic, only compose existing pieces with Indian-market defaults
(nse_india_config, BSE calendar, IST timezone).
"""

from __future__ import annotations

from ml4t.india.workflows.deployment import DeploymentPipeline
from ml4t.india.workflows.research import ResearchPipeline

__all__ = [
    "DeploymentPipeline",
    "ResearchPipeline",
]

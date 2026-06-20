"""Regression tests that build every registry preset against the *real*
upstream ``ml4t.models`` API.

Unlike :mod:`tests.unit.test_india_models`, which patches a recording
stub into ``sys.modules`` (and therefore cannot catch upstream-API
drift), these tests call the genuine ``ml4t.models`` constructors. They
reproduce the exact construction path exercised by
``examples/end_to_end.py`` stage 5::

    resolve_preset(name).pipeline_factory()

This guards against the upstream migration from kwargs-style
constructors (``PCAModel(n_factors=5, ...)``) to config-dataclass
constructors (``PCAModel(PCAConfig(n_factors=5))``), plus the sibling
drift in the forecaster (``window`` removed) and the pipeline
(``mapper`` now required).

The construction is purely in-memory: no Kite credentials, no network,
and no fitting against data -- so the suite is CI-safe.
"""

from __future__ import annotations

import pytest

# Skip cleanly if the upstream model stack isn't installed in the test
# environment (e.g. a broker-only install). The bug this guards against
# only manifests when the real ml4t.models is importable.
pytest.importorskip("ml4t.models")

from ml4t.india.models.registry import list_presets, resolve_preset


@pytest.mark.parametrize("name", list_presets())
def test_preset_pipeline_factory_constructs(name: str) -> None:
    """Every registered preset's ``pipeline_factory()`` builds cleanly.

    This is the exact call ``examples/end_to_end.py`` makes at stage 5.
    Before the config-dataclass fix it raised
    ``TypeError: PCAModel.__init__() got an unexpected keyword argument
    'n_factors'`` (and analogous TypeErrors for RPPCA/IPCA).
    """
    pipeline = resolve_preset(name).pipeline_factory()
    assert pipeline is not None
    # The latent-factor presets all assemble a pipeline that carries the
    # model and forecaster we configured.
    assert hasattr(pipeline, "model")
    assert hasattr(pipeline, "forecaster")


def test_all_presets_covered() -> None:
    """Guard that the three shipped presets are exercised above."""
    assert set(list_presets()) >= {
        "nse_cash_long_only",
        "nse_fno_delta_neutral",
        "nse_sector_rotation",
    }

"""Contract tests for :class:`ml4t.india.data.base.IndianOHLCVProvider`.

These tests focus on the *inheritance contract* -- that is, what has to
remain true for downstream concrete providers (e.g. the future
``KiteProvider``) to benefit from the upstream
:class:`ml4t.data.providers.base.BaseProvider` template method.

What we do **not** test here:

* Actual OHLCV fetching. That requires either network or mocks of the
  broker SDK and belongs to Phase 2's KiteProvider tests.
* Validation behaviour of the upstream pipeline -- upstream's own test
  suite already covers that. We only verify that our subclass does not
  accidentally break the pipeline by overriding the wrong method.
"""

from __future__ import annotations

import pytest
from ml4t.data.providers.base import BaseProvider

from ml4t.india.data import IndianOHLCVProvider

pytestmark = pytest.mark.contract


class TestInheritanceContract:
    def test_extends_upstream_base_provider(self) -> None:
        """LSP: every IndianOHLCVProvider must be a BaseProvider."""
        assert issubclass(IndianOHLCVProvider, BaseProvider)

    def test_timezone_default_is_asia_kolkata(self) -> None:
        assert IndianOHLCVProvider.TIMEZONE == "Asia/Kolkata"

    def test_supported_exchanges_default_is_empty(self) -> None:
        """Abstract class declares no coverage; concrete subclasses override."""
        assert IndianOHLCVProvider.SUPPORTED_EXCHANGES == frozenset()

    def test_name_remains_abstract(self) -> None:
        """We deliberately do NOT override BaseProvider.name.

        If this regresses, concrete subclasses lose the compile-time safety
        that forces them to pick a provider name.
        """
        assert "name" in IndianOHLCVProvider.__abstractmethods__

    def test_cannot_instantiate_directly(self) -> None:
        """Abstract classes must not be instantiated; Python enforces this."""
        with pytest.raises(TypeError):
            IndianOHLCVProvider()  # type: ignore[abstract]

    def test_fetch_ohlcv_template_method_inherited(self) -> None:
        """We consume upstream's template method without overriding it.

        Asserting identity here guards against a future refactor that
        accidentally shadows the template method -- which would silently
        skip rate limiting, validation, and retry.
        """
        assert IndianOHLCVProvider.fetch_ohlcv is BaseProvider.fetch_ohlcv

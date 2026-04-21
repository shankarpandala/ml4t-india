"""Contract tests for :class:`ml4t.india.data.base.IndianOHLCVProvider`.

These tests focus on the *inheritance contract* -- that is, what has to
remain true for downstream concrete providers (e.g. the future
``KiteProvider``) to benefit from the upstream
:class:`ml4t.data.providers.base.BaseProvider` template method.

What we do **not** test here:

* Actual OHLCV fetching. That requires either network or mocks of the
  broker SDK and belongs to Phase 2's KiteProvider tests.
* Whether upstream marks particular methods as ``@abstractmethod``. The
  concrete abstractness decision belongs to upstream; our contract is
  only about what WE add on top. Checks below use ``vars(cls)`` to
  introspect our class body directly, not ``__abstractmethods__`` which
  depends on upstream versioning.
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

    def test_name_not_shadowed_in_our_class_body(self) -> None:
        """Concrete subclasses -- not us -- must supply the provider name.

        Upstream may or may not mark ``name`` as ``@abstractmethod``; that
        is upstream's concern and can vary across versions. What we assert
        here is only that our class does NOT define ``name`` itself. A
        future refactor that accidentally hard-codes a provider name on
        the India-layer abstract would trip this test.
        """
        assert "name" not in vars(IndianOHLCVProvider)

    def test_fetch_ohlcv_not_shadowed_in_our_class_body(self) -> None:
        """We consume upstream's template method without shadowing it.

        Checks the class body (``vars()``) rather than attribute lookup:
        the latter can be confused by decorators like ``@tenacity.retry``
        that upstream uses on ``fetch_ohlcv``. What we actually care about
        is that ``IndianOHLCVProvider`` does NOT re-define the method
        itself -- if a future refactor did that, rate-limiting,
        validation, and retry would all silently stop running.
        """
        assert "fetch_ohlcv" not in vars(IndianOHLCVProvider)

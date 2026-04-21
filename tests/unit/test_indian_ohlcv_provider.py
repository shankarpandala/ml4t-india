"""Contract tests for :class:`ml4t.india.data.base.IndianOHLCVProvider`.

These tests focus on the *inheritance contract* -- what has to remain
true for downstream concrete providers (``KiteProvider`` in Phase 2, the
future bhavcopy providers, and eventual additional-broker providers) to
benefit from the upstream
:class:`ml4t.data.providers.base.BaseProvider` template method.

What we do **not** test here:

* Actual OHLCV fetching. That requires either network or mocks of the
  broker SDK and belongs to Phase 2's KiteProvider tests, where we can
  drive end-to-end behaviour through :class:`FakeKiteClient`.
* Validation behaviour of the upstream pipeline -- upstream's own test
  suite covers that. We only assert that our subclass does not
  accidentally break the pipeline by shadowing the wrong method.

The upstream-API drift guard for these invariants lives separately in
``tests/contracts/test_upstream_api_snapshot.py``; when upstream changes
shape, THAT file fails first (clearly pointing at the upstream drift)
rather than making the guarantees below look like ml4t-india bugs.
"""

from __future__ import annotations

import inspect

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
        assert frozenset() == IndianOHLCVProvider.SUPPORTED_EXCHANGES


class TestAbstractness:
    def test_class_is_abstract(self) -> None:
        """IndianOHLCVProvider inherits BaseProvider's abstract `name` and
        does not implement it, so the class itself stays abstract."""
        assert inspect.isabstract(IndianOHLCVProvider)

    def test_name_remains_abstract(self) -> None:
        """Guard: we must NOT accidentally implement `name` in our class
        body. Doing so would silently let every concrete subclass skip
        declaring a provider name, which is the key identifier used in
        storage paths and logs."""
        assert "name" in IndianOHLCVProvider.__abstractmethods__

    def test_cannot_instantiate_directly(self) -> None:
        """Python's ABCMeta enforces the abstractness at instantiation."""
        with pytest.raises(TypeError):
            IndianOHLCVProvider()  # type: ignore[abstract]


class TestTemplateMethodNotShadowed:
    """Our class must NOT redefine upstream's template-method glue.

    We use ``vars(cls)`` -- which returns only names defined on this
    class, not inherited ones -- because attribute lookup via MRO can
    be confused by ``@tenacity.retry`` or similar descriptor wrappers
    upstream uses on ``fetch_ohlcv``. The class-body check is robust
    against every such layering trick and gives the exact guarantee
    we want: 'we did not override this method ourselves'.
    """

    def test_name_not_shadowed_in_our_class_body(self) -> None:
        """Concrete subclasses -- not us -- must supply the provider name."""
        assert "name" not in vars(IndianOHLCVProvider)

    def test_fetch_ohlcv_not_shadowed_in_our_class_body(self) -> None:
        """Shadowing the template method would bypass upstream's
        rate-limiting, validation, and retry pipeline entirely."""
        assert "fetch_ohlcv" not in vars(IndianOHLCVProvider)

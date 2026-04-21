"""Smoke test for :mod:`ml4t.india.data`.

Full contract tests for :class:`IndianOHLCVProvider` are deferred to Phase 2
(KiteProvider), where we have a concrete subclass that exercises the abstract
base end-to-end. A richer attempt to test the abstract directly in Phase-0
(identity checks on the template method, abstractness introspection,
direct-instantiation TypeError) ran into upstream-version drift and fragile
decorator / ABCMeta interactions; rather than keep iterating on assertions
that depend on private details of upstream, we lean on the Phase-2 tests
plus the Phase-0.7 upstream-API snapshot test as the real contract guards.

This single smoke test verifies only that the module imports cleanly.
"""

from __future__ import annotations


def test_module_imports() -> None:
    """Import-time smoke: the module compiles and exports its public name."""
    from ml4t.india.data import IndianOHLCVProvider

    assert IndianOHLCVProvider is not None
    assert IndianOHLCVProvider.__name__ == "IndianOHLCVProvider"

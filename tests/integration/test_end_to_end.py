"""Credential-gated integration test for the real-data orchestrator.

This drives ``examples/end_to_end.py`` end-to-end against the REAL Zerodha
Kite market. It is doubly gated and SKIPS (never fails) unless BOTH:

* ``ML4T_INDIA_E2E_REAL=1`` is set, AND
* the five Kite credentials are present in the OS keychain.

So it never runs -- and never fails -- in CI without credentials. Run it
locally with::

    ML4T_INDIA_E2E_REAL=1 pytest -m integration tests/integration/test_end_to_end.py

The orchestrator's only simulated component is order execution; this test
asserts the run completes a real login + real data pull and reaches the
deploy gate without ever placing a live order.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

pytestmark = pytest.mark.integration

_KEYCHAIN_SERVICE = "ml4t-india"
_REQUIRED_KEYS = (
    "kite_api_key",
    "kite_api_secret",
    "kite_user_id",
    "kite_password",
    "kite_totp_secret",
)


def _creds_present() -> bool:
    try:
        import keyring
    except ImportError:
        return False
    try:
        return all(keyring.get_password(_KEYCHAIN_SERVICE, key) for key in _REQUIRED_KEYS)
    except Exception:  # noqa: BLE001 -- a flaky keychain backend should skip, not error
        return False


def _gate_reason() -> str | None:
    if os.environ.get("ML4T_INDIA_E2E_REAL") != "1":
        return "set ML4T_INDIA_E2E_REAL=1 to run the real end-to-end test"
    if not _creds_present():
        return "Kite credentials are not present in the OS keychain"
    return None


def _load_orchestrator():
    """Import examples/end_to_end.py by path (it lives outside the package)."""
    import importlib.util

    repo_root = Path(__file__).resolve().parents[2]
    script = repo_root / "examples" / "end_to_end.py"
    spec = importlib.util.spec_from_file_location("ml4t_india_e2e", script)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_end_to_end_runs_on_real_data() -> None:
    reason = _gate_reason()
    if reason is not None:
        pytest.skip(reason)

    import asyncio

    orchestrator = _load_orchestrator()
    # _run() returns 0 (ready) or 2 (not ready); both mean the staged flow
    # completed on real data without raising. A live order is impossible by
    # construction (paper broker). Anything other than a clean int return is
    # a real failure.
    exit_code = asyncio.run(orchestrator._run())
    assert exit_code in (0, 2)


def test_orchestrator_imports_without_credentials() -> None:
    """The script must be importable with no creds (it only logs in on run)."""
    module = _load_orchestrator()
    assert hasattr(module, "_run")
    assert hasattr(module, "ensure_token")

"""Session-scoped fixtures for Kite live-broker integration tests.

All tests in this package auto-skip when any credential is missing from
the OS keychain. Run `python scripts/store_kite_credentials.py` first.
"""

from __future__ import annotations

from dataclasses import dataclass

import pytest

try:
    import keyring
    import keyring.errors

    _KEYRING_AVAILABLE = True
except ImportError:
    _KEYRING_AVAILABLE = False

from ml4t.india.kite.client import AsyncKiteClient
from ml4t.india.live.kite_broker import KiteBroker

_SERVICE = "ml4t-india"
_REQUIRED = ["kite_api_key", "kite_api_secret", "kite_request_token", "kite_access_token"]
_SKIP_MSG = (
    "Kite credentials not found in keychain — "
    "run: python scripts/store_kite_credentials.py"
)


def _get_credential(key: str) -> str | None:
    """Return a keychain value, or None if absent or no backend available."""
    try:
        return keyring.get_password(_SERVICE, key)
    except keyring.errors.NoKeyringError:
        return None


@dataclass
class KiteCredentials:
    api_key: str
    api_secret: str
    request_token: str
    access_token: str


@pytest.fixture(scope="session")
def kite_credentials() -> KiteCredentials:
    if not _KEYRING_AVAILABLE:
        pytest.skip("keyring package not installed — run: pip install keyring>=25")

    missing = [k for k in _REQUIRED if not _get_credential(k)]
    if missing:
        pytest.skip(f"{_SKIP_MSG} (missing: {', '.join(missing)})")

    return KiteCredentials(
        api_key=_get_credential("kite_api_key"),  # type: ignore[arg-type]
        api_secret=_get_credential("kite_api_secret"),  # type: ignore[arg-type]
        request_token=_get_credential("kite_request_token"),  # type: ignore[arg-type]
        access_token=_get_credential("kite_access_token"),  # type: ignore[arg-type]
    )


@pytest.fixture(scope="session")
async def kite_broker(kite_credentials: KiteCredentials) -> KiteBroker:
    client = AsyncKiteClient.from_api_key(
        api_key=kite_credentials.api_key,
        access_token=kite_credentials.access_token,
    )
    broker = KiteBroker(client=client)
    await broker.connect()
    yield broker  # type: ignore[misc]
    await broker.disconnect()

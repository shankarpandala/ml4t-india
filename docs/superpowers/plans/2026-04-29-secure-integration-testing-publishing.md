# ml4t-india: Secure Integration Testing + Publishing Readiness

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add OS-keychain Kite credential management, a local-only real-broker smoke test suite, a comprehensive 12-section tour notebook, updated documentation across all relevant files, and a tag-triggered PyPI OIDC + conda-forge publish pipeline.

**Architecture:** Credentials stored exclusively in the OS keychain via `keyring` (Windows Credential Manager locally, libsecret on VPS). Integration tests live in `tests/integration/` and auto-skip when credentials are absent — CI never sees them. A one-time setup script handles both first-time storage and daily access-token refresh. The publish workflow fires automatically on `v*` tag pushes using PyPI Trusted Publishers (OIDC, no stored secrets).

**Tech Stack:** `keyring>=25`, `kiteconnect>=5`, `ml4t.india.kite.auth` (`generate_session`, `login_url`), `ml4t.india.kite.client.AsyncKiteClient` (factory: `from_api_key`), `ml4t.india.live.kite_broker.KiteBroker`, `pypa/gh-action-pypi-publish@release/v1`, `pytest>=8` with `asyncio_mode=auto`

---

## File Map

| Action  | Path                                              | Responsibility                                        |
|---------|---------------------------------------------------|-------------------------------------------------------|
| Modify  | `pyproject.toml`                                  | Add `keyring>=25` to `[dev]` extras                   |
| Create  | `scripts/store_kite_credentials.py`               | Interactive credential storage + daily token refresh  |
| Create  | `tests/integration/__init__.py`                   | Package marker (empty)                                |
| Create  | `tests/integration/conftest.py`                   | Keychain-reading fixtures; auto-skip when absent      |
| Create  | `tests/integration/test_kite_smoke.py`            | Full real-broker smoke test                           |
| Create  | `notebooks/11-full-live-integration.ipynb`        | Comprehensive 12-section library tour                 |
| Create  | `docs/integration-testing.md`                     | Credential setup + running integration tests guide    |
| Create  | `docs/releasing.md`                               | Tag → PyPI → conda-forge release workflow             |
| Modify  | `README.md`                                       | Update status; add Integration Testing + Security     |
| Modify  | `AGENTS.md`                                       | Expand Testing section with integration instructions  |
| Modify  | `docs/quickstart.md`                              | Add Real Broker Connection section                    |
| Modify  | `CHANGELOG.md`                                    | Add Phase-1 additions + integration work              |
| Create  | `.github/workflows/publish.yml`                   | Tag-triggered PyPI OIDC publish + conda validation    |
| Create  | `conda-recipe/meta.yaml`                          | conda-forge recipe for staged-recipes submission      |

---

### Task 1: Add `keyring` to dev dependencies

**Files:**
- Modify: `pyproject.toml`

- [ ] **Step 1: Add keyring to the dev extras list**

In `pyproject.toml`, find the `dev` entry under `[project.optional-dependencies]` and append `"keyring>=25"`:

```toml
dev = [
    "pytest>=8",
    "pytest-asyncio>=0.23",
    "pytest-cov>=4.1",
    "pytest-timeout>=2.1",
    "hypothesis>=6.80",
    "vcrpy>=6",
    "respx>=0.21",
    "ruff>=0.8",
    "ty",
    "pre-commit>=3",
    "keyring>=25",
]
```

- [ ] **Step 2: Install and verify**

```bash
pip install --pre -e '.[dev]'
python -c "import keyring; print('keyring version:', keyring.__version__)"
```

Expected: prints a version string like `25.x.x` with no errors.

- [ ] **Step 3: Commit**

```bash
git add pyproject.toml
git commit -m "chore: add keyring>=25 to dev dependencies"
```

---

### Task 2: Create credential setup script

**Files:**
- Create: `scripts/store_kite_credentials.py`

The script uses only stdlib + `keyring` + conditionally `ml4t.india.kite.auth` (which is always available in dev). It must not import anything else from the project at the module level.

- [ ] **Step 1: Create the `scripts/` directory**

```bash
mkdir -p scripts
```

- [ ] **Step 2: Write `scripts/store_kite_credentials.py`**

```python
"""One-time credential setup for ml4t-india integration tests.

Stores Kite Connect credentials in the OS keychain:
  - Windows: Windows Credential Manager (search "ml4t-india")
  - Linux:   GNOME Keyring / libsecret
  - macOS:   Keychain Access

Usage:
    python scripts/store_kite_credentials.py           # first-time setup
    python scripts/store_kite_credentials.py --refresh # daily token refresh
    python scripts/store_kite_credentials.py --verify  # print masked values
    python scripts/store_kite_credentials.py --clear   # delete all entries
"""

from __future__ import annotations

import argparse
import getpass
import sys

try:
    import keyring
    import keyring.errors
except ImportError:
    print("ERROR: keyring not installed. Run: pip install keyring>=25")
    sys.exit(1)

_SERVICE = "ml4t-india"
_ALL_KEYS = ["kite_api_key", "kite_api_secret", "kite_request_token", "kite_access_token"]


def _mask(value: str) -> str:
    if len(value) <= 6:
        return "***"
    return value[:3] + "*" * (len(value) - 6) + value[-3:]


def _get(key: str) -> str | None:
    return keyring.get_password(_SERVICE, key)


def _set(key: str, value: str) -> None:
    keyring.set_password(_SERVICE, key, value)


def _delete(key: str) -> bool:
    try:
        keyring.delete_password(_SERVICE, key)
        return True
    except keyring.errors.PasswordDeleteError:
        return False


def cmd_store(refresh_only: bool = False) -> None:
    if not refresh_only:
        print("=== ml4t-india Kite credential setup ===")
        print("Credentials go to the OS keychain — nothing is written to disk or git.\n")

        api_key = getpass.getpass("Kite API key: ").strip()
        if not api_key:
            print("ERROR: API key cannot be empty.")
            sys.exit(1)
        api_secret = getpass.getpass("Kite API secret: ").strip()
        if not api_secret:
            print("ERROR: API secret cannot be empty.")
            sys.exit(1)

        _set("kite_api_key", api_key)
        _set("kite_api_secret", api_secret)
        print("\nAPI key and secret stored in keychain.")

        try:
            from ml4t.india.kite.auth import login_url
            url = login_url(api_key)
        except ImportError:
            url = f"https://kite.trade/connect/login?api_key={api_key}&v=3"

        print(f"\nOpen this URL in your browser to log in:")
        print(f"  {url}")
        print("\nAfter login you are redirected to your app URL with ?request_token=XXX in the URL.")
        print("Copy that token and paste it below.\n")
    else:
        print("=== Daily token refresh ===")

    api_key = _get("kite_api_key")
    api_secret = _get("kite_api_secret")
    if not api_key or not api_secret:
        print("ERROR: API key/secret not found. Run without --refresh first.")
        sys.exit(1)

    request_token = getpass.getpass("Request token (from browser redirect URL): ").strip()
    if not request_token:
        print("ERROR: Request token cannot be empty.")
        sys.exit(1)
    _set("kite_request_token", request_token)

    print("Generating access token...")
    try:
        from ml4t.india.kite.auth import generate_session
        record = generate_session(api_key, api_secret, request_token)
        access_token = record.access_token
    except ImportError:
        from kiteconnect import KiteConnect
        kite = KiteConnect(api_key=api_key)
        data = kite.generate_session(request_token=request_token, api_secret=api_secret)
        access_token = data["access_token"]

    _set("kite_access_token", access_token)
    print("Access token stored. Valid until ~06:00 IST tomorrow.")
    print("\nRun: pytest -m integration -v")


def cmd_verify() -> None:
    print("=== Stored credentials (masked) ===")
    all_present = True
    for key in _ALL_KEYS:
        value = _get(key)
        if value:
            print(f"  {key}: {_mask(value)}")
        else:
            print(f"  {key}: NOT SET")
            all_present = False
    if all_present:
        print("\nAll credentials stored. Ready to run: pytest -m integration -v")
    else:
        print("\nSome credentials missing. Run: python scripts/store_kite_credentials.py")


def cmd_clear() -> None:
    print("Clearing all ml4t-india credentials from keychain...")
    for key in _ALL_KEYS:
        if _delete(key):
            print(f"  Deleted: {key}")
        else:
            print(f"  Not found (already clear): {key}")
    print("Done.")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Manage ml4t-india Kite credentials in the OS keychain."
    )
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--refresh", action="store_true", help="Refresh the daily access token only")
    group.add_argument("--verify", action="store_true", help="Print masked stored credentials")
    group.add_argument("--clear", action="store_true", help="Delete all stored credentials")
    args = parser.parse_args()

    if args.verify:
        cmd_verify()
    elif args.clear:
        cmd_clear()
    elif args.refresh:
        cmd_store(refresh_only=True)
    else:
        cmd_store(refresh_only=False)


if __name__ == "__main__":
    main()
```

- [ ] **Step 3: Verify --verify runs with no credentials (expected: all NOT SET)**

```bash
python scripts/store_kite_credentials.py --verify
```

Expected output:
```
=== Stored credentials (masked) ===
  kite_api_key: NOT SET
  kite_api_secret: NOT SET
  kite_request_token: NOT SET
  kite_access_token: NOT SET

Some credentials missing. Run: python scripts/store_kite_credentials.py
```

- [ ] **Step 4: Verify --clear runs without errors**

```bash
python scripts/store_kite_credentials.py --clear
```

Expected:
```
Clearing all ml4t-india credentials from keychain...
  Not found (already clear): kite_api_key
  Not found (already clear): kite_api_secret
  Not found (already clear): kite_request_token
  Not found (already clear): kite_access_token
Done.
```

- [ ] **Step 5: Commit**

```bash
git add scripts/store_kite_credentials.py
git commit -m "feat(scripts): Kite credential setup + daily token refresh via OS keychain"
```

---

### Task 3: Integration test infrastructure

**Files:**
- Create: `tests/integration/__init__.py`
- Create: `tests/integration/conftest.py`

- [ ] **Step 1: Create `tests/integration/__init__.py`**

Create an empty file:

```bash
python -c "open('tests/integration/__init__.py', 'w').close()"
```

- [ ] **Step 2: Write `tests/integration/conftest.py`**

```python
"""Session-scoped fixtures for Kite live-broker integration tests.

All tests in this package auto-skip when any credential is missing from
the OS keychain. Run `python scripts/store_kite_credentials.py` first.
"""

from __future__ import annotations

from dataclasses import dataclass

import pytest

try:
    import keyring
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

    missing = [k for k in _REQUIRED if not keyring.get_password(_SERVICE, k)]
    if missing:
        pytest.skip(f"{_SKIP_MSG} (missing: {', '.join(missing)})")

    return KiteCredentials(
        api_key=keyring.get_password(_SERVICE, "kite_api_key"),        # type: ignore[arg-type]
        api_secret=keyring.get_password(_SERVICE, "kite_api_secret"),  # type: ignore[arg-type]
        request_token=keyring.get_password(_SERVICE, "kite_request_token"),  # type: ignore[arg-type]
        access_token=keyring.get_password(_SERVICE, "kite_access_token"),    # type: ignore[arg-type]
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
```

- [ ] **Step 3: Verify the conftest is importable and collection works**

```bash
pytest tests/integration/ -v --collect-only
```

Expected: either skip messages (no credentials) or test names listed. No import errors.

- [ ] **Step 4: Commit**

```bash
git add tests/integration/__init__.py tests/integration/conftest.py
git commit -m "test(integration): conftest with keychain fixtures and auto-skip on missing credentials"
```

---

### Task 4: Kite live broker smoke test

**Files:**
- Create: `tests/integration/test_kite_smoke.py`

- [ ] **Step 1: Write the test file**

```python
"""Full real-broker smoke test for KiteBroker against live Kite Connect.

Run with:
    pytest -m integration -v

Requires credentials stored via:
    python scripts/store_kite_credentials.py

The order round-trip uses a ₹1 LIMIT BUY for 1 share of INFY — far off-market,
zero fill risk. All assertions are structural (types and presence), never price-based.
"""

from __future__ import annotations

import pytest
from ml4t.backtest.types import OrderSide, OrderStatus, OrderType

from ml4t.india.live.kite_broker import KiteBroker


@pytest.mark.integration
class TestKiteLiveBroker:

    async def test_is_connected(self, kite_broker: KiteBroker) -> None:
        assert await kite_broker.is_connected_async() is True

    async def test_get_cash(self, kite_broker: KiteBroker) -> None:
        cash = await kite_broker.get_cash_async()
        assert isinstance(cash, float)
        assert cash >= 0.0

    async def test_get_account_value(self, kite_broker: KiteBroker) -> None:
        value = await kite_broker.get_account_value_async()
        assert isinstance(value, float)
        assert value >= 0.0

    async def test_get_positions(self, kite_broker: KiteBroker) -> None:
        positions = await kite_broker.get_positions_async()
        assert isinstance(positions, dict)
        for asset, pos in positions.items():
            assert ":" in asset, f"asset missing EXCHANGE: prefix: {asset!r}"
            assert pos.asset == asset
            assert pos.quantity != 0

    async def test_order_roundtrip(self, kite_broker: KiteBroker) -> None:
        # Place far-off-market limit order — ₹1 guarantees zero fill
        order = await kite_broker.submit_order_async(
            asset="NSE:INFY",
            quantity=1,
            side=OrderSide.BUY,
            order_type=OrderType.LIMIT,
            limit_price=1.0,
            product="CNC",
        )
        assert order.order_id, "Expected a non-empty order_id from Kite"
        assert order.status == OrderStatus.PENDING
        assert order.asset == "NSE:INFY"
        assert order.side == OrderSide.BUY
        assert order.quantity == 1.0
        assert order.limit_price == 1.0

        # Confirm the order appears in the pending list
        pending = await kite_broker.get_pending_orders_async()
        pending_ids = [o.order_id for o in pending]
        assert order.order_id in pending_ids, (
            f"Order {order.order_id!r} not found in pending orders: {pending_ids}"
        )

        # Cancel and verify acceptance
        cancelled = await kite_broker.cancel_order_async(order.order_id)
        assert cancelled is True

    async def test_disconnect(self, kite_broker: KiteBroker) -> None:
        await kite_broker.disconnect()
        assert await kite_broker.is_connected_async() is False
```

- [ ] **Step 2: Run without credentials — confirm all tests skip cleanly**

```bash
pytest tests/integration/test_kite_smoke.py -v
```

Expected output (no credentials stored):
```
SKIPPED [5] tests/integration/conftest.py:NN: Kite credentials not found in keychain ...
5 skipped in X.XXs
```

- [ ] **Step 3: (After running `python scripts/store_kite_credentials.py`) Run live**

```bash
pytest -m integration -v
```

Expected: `5 passed` (or skip if credentials not set).

- [ ] **Step 4: Commit**

```bash
git add tests/integration/test_kite_smoke.py
git commit -m "test(integration): Kite live broker smoke test — connect/account/positions/order-cancel/disconnect"
```

---

### Task 5: Comprehensive tour notebook

**Files:**
- Create: `notebooks/11-full-live-integration.ipynb`

The notebook uses `getpass` for all credential input — no keychain dependency, works on any machine. Create it as a valid `.ipynb` JSON file with the cells below. Use `nbformat` cell types: `"markdown"` or `"code"`.

- [ ] **Step 1: Create the notebook**

Create `notebooks/11-full-live-integration.ipynb` with the following structure (valid nbformat v4 JSON). Each cell is listed as `[TYPE] content`:

**Cell 1 [markdown]:**
```
# ml4t-india — Full Live Integration Tour

Exercises **every feature** of the library with a real Kite Connect session.

**Prerequisites:** `pip install ml4t-india[all]` and an active Kite Connect subscription.

Credentials are entered interactively — nothing is stored to disk or git.
```

**Cell 2 [code] — Imports:**
```python
import asyncio
import datetime as dt
import getpass

from ml4t.india.kite.auth import generate_session, login_url
from ml4t.india.kite.client import AsyncKiteClient, KiteClient
from ml4t.india.live.kite_broker import KiteBroker
from ml4t.india.live.kite_ticker_feed import KiteTickerFeed
from ml4t.india.live.postbacks import PostbackHandler
from ml4t.india.backtest.charges import ZerodhaChargesModel, Segment
from ml4t.india.backtest.lot_sizing import round_to_lot, floor_to_lot
from ml4t.india.calendar import nse_calendar
from ml4t.india.options import OptionChain, compute_greeks
from ml4t.india.workflows import ResearchPipeline, DeploymentPipeline
from ml4t.backtest.types import OrderSide, OrderType
```

**Cell 3 [markdown]:** `## Section 1: Login & Auth`

**Cell 4 [code] — Credential input + session:**
```python
print("Enter your Kite Connect credentials (not stored anywhere):")
api_key = getpass.getpass("API key: ").strip()
api_secret = getpass.getpass("API secret: ").strip()

print(f"\nOpen this URL to log in:\n  {login_url(api_key)}")
print("\nAfter redirect, copy the ?request_token=XXX value from the URL.")
request_token = getpass.getpass("Request token: ").strip()

record = generate_session(api_key, api_secret, request_token)
print(f"Logged in as: {record.user_id}")
print(f"Token expires: ~06:00 IST tomorrow | is_expired={record.is_expired()}")
```

**Cell 5 [code] — Build async client:**
```python
sync_client = KiteClient.from_api_key(api_key=api_key, access_token=record.access_token)
client = AsyncKiteClient(sync_client)

profile = await client.profile()
print(f"User: {profile.get('user_name')} | Email: {profile.get('email')} | Broker: {profile.get('broker')}")
```

**Cell 6 [markdown]:** `## Section 2: Instruments`

**Cell 7 [code]:**
```python
nse_instruments = await client.instruments("NSE")
print(f"NSE instruments: {len(nse_instruments)}")

infy = next(i for i in nse_instruments if i.get("tradingsymbol") == "INFY")
infy_token = infy["instrument_token"]
print(f"INFY: token={infy_token}, lot_size={infy.get('lot_size', 1)}, exchange={infy.get('exchange')}")
```

**Cell 8 [markdown]:** `## Section 3: Historical OHLCV`

**Cell 9 [code]:**
```python
end = dt.datetime.now(dt.UTC)
start = end - dt.timedelta(days=30)
candles = await client.historical_data(
    instrument_token=infy_token,
    from_date=start,
    to_date=end,
    interval="day",
)
print(f"INFY daily candles (30 days): {len(candles)} bars")
for c in candles[-3:]:
    print(f"  {c['date'].date()} O={c['open']} H={c['high']} L={c['low']} C={c['close']} V={c['volume']}")
assert len(candles) > 0, "Expected at least one candle"
```

**Cell 10 [markdown]:** `## Section 4: NSE Calendar`

**Cell 11 [code]:**
```python
cal = nse_calendar()
today = dt.date.today()
print(f"Today ({today}) is_session_day: {cal.is_session_day(today)}")
next_sess = cal.next_session(today)
print(f"Next session: {next_sess}")
open_t, close_t = cal.session_bounds(next_sess)
print(f"Session bounds (IST): {open_t.time()} — {close_t.time()}")
assert cal is not None
```

**Cell 12 [markdown]:** `## Section 5: Backtest + Charges`

**Cell 13 [code]:**
```python
model = ZerodhaChargesModel(default_segment=Segment.EQUITY_DELIVERY)
charges = model.calculate(asset="NSE:INFY", quantity=10, price=1800.0)
print(f"Charges on 10 x INFY @ ₹1800 (delivery):")
print(f"  Brokerage: ₹{charges.brokerage:.2f}")
print(f"  STT:       ₹{charges.stt:.2f}")
print(f"  GST:       ₹{charges.gst:.2f}")
print(f"  Total:     ₹{charges.total:.2f}")
assert charges.total >= 0
```

**Cell 14 [markdown]:** `## Section 6: Lot Sizing`

**Cell 15 [code]:**
```python
lot_size = 50  # NIFTY standard lot size
print(f"round_to_lot(76, 50) = {round_to_lot(76, 50)}")   # → 100
print(f"floor_to_lot(76, 50) = {floor_to_lot(76, 50)}")   # → 50
assert round_to_lot(76, 50) == 100
assert floor_to_lot(76, 50) == 50
```

**Cell 16 [markdown]:** `## Section 7: Option Chain`

**Cell 17 [code]:**
```python
nifty_options = [
    i for i in nse_instruments
    if i.get("name") == "NIFTY" and i.get("instrument_type") in ("CE", "PE")
]
expiries = sorted({i["expiry"] for i in nifty_options if i.get("expiry")})
nearest_expiry = expiries[0]
print(f"Nearest NIFTY expiry: {nearest_expiry}")

chain = OptionChain.from_instruments(nifty_options, underlying="NIFTY", expiry=nearest_expiry)

nifty_ltp_data = await client.ltp(["NSE:NIFTY 50"])
spot = list(nifty_ltp_data.values())[0]["last_price"]
atm = chain.atm_strike(spot)
calls, puts = chain.around_atm(spot, count=3)
print(f"Spot={spot}  ATM={atm}  PCR={chain.put_call_ratio():.3f}  MaxPain={chain.max_pain()}")
print(f"Near-ATM calls: {[s.strike for s in calls]}")
print(f"Near-ATM puts:  {[s.strike for s in puts]}")
```

**Cell 18 [markdown]:** `## Section 8: Greeks (Black-Scholes)`

**Cell 19 [code]:**
```python
tte = (nearest_expiry - dt.date.today()).days / 365

greeks = compute_greeks(
    flag="CE",
    spot=spot,
    strike=float(atm),
    time_to_expiry=tte,
    risk_free_rate=0.065,
    volatility=0.15,
)
print(f"ATM Call Greeks  spot={spot}  strike={atm}  tte={tte:.4f}")
print(f"  Delta={greeks.delta:.4f}  Gamma={greeks.gamma:.6f}  Theta={greeks.theta:.4f}  Vega={greeks.vega:.4f}")
assert greeks.delta is not None
```

**Cell 20 [markdown]:** `## Section 9: Live Broker (KiteBroker)`

**Cell 21 [code] — Account info:**
```python
broker = KiteBroker(client=client)
await broker.connect()
assert await broker.is_connected_async(), "Broker failed to connect"

cash = await broker.get_cash_async()
account_value = await broker.get_account_value_async()
positions = await broker.get_positions_async()
print(f"Cash:          ₹{cash:,.2f}")
print(f"Account value: ₹{account_value:,.2f}")
print(f"Open positions: {len(positions)}")
for asset, pos in positions.items():
    print(f"  {asset}: qty={pos.quantity} @ ₹{pos.entry_price:.2f}")
```

**Cell 22 [code] — Order round-trip:**
```python
print("Placing ₹1 LIMIT BUY for 1 INFY (far off-market, zero fill risk)...")
order = await broker.submit_order_async(
    asset="NSE:INFY",
    quantity=1,
    side=OrderSide.BUY,
    order_type=OrderType.LIMIT,
    limit_price=1.0,
    product="CNC",
)
print(f"Order placed: id={order.order_id}  status={order.status}")

pending = await broker.get_pending_orders_async()
assert any(o.order_id == order.order_id for o in pending), "Order not in pending list"
print(f"Confirmed in pending orders ({len(pending)} total)")

cancelled = await broker.cancel_order_async(order.order_id)
assert cancelled is True
print(f"Order cancelled: {cancelled}")

await broker.disconnect()
print("KiteBroker disconnected")
```

**Cell 23 [markdown]:** `## Section 10: Ticker Feed (KiteTickerFeed)`

**Cell 24 [code]:**
```python
import threading

ticks_received: list[dict] = []
connect_event = threading.Event()

feed = KiteTickerFeed(api_key=api_key, access_token=record.access_token, default_mode="ltp")

def _on_connect() -> None:
    feed.subscribe([infy_token], mode="ltp")
    connect_event.set()

def _on_ticks(ticks: list[dict]) -> None:
    ticks_received.extend(ticks)

feed.on_connect(_on_connect)
feed.on_ticks(_on_ticks)

await feed.start()
connected = connect_event.wait(timeout=15)

if connected and len(ticks_received) == 0:
    # Wait briefly for a tick batch (market may be closed; that's ok)
    await asyncio.sleep(3)

feed.stop()
print(f"KiteTickerFeed: connected={connected}  ticks_received={len(ticks_received)}")
if ticks_received:
    print(f"  Sample tick: {ticks_received[0]}")
else:
    print("  No ticks (market closed or outside trading hours — feed connectivity verified)")
```

**Cell 25 [markdown]:** `## Section 11: Research Pipeline`

**Cell 26 [code]:**
```python
from ml4t.india.data.kite import KiteProvider

provider = KiteProvider(client=sync_client)
research = ResearchPipeline(provider=provider, initial_cash=1_000_000)
print(f"ResearchPipeline: {research}")
print("Full run requires a Strategy subclass — see notebooks/04-backtest-with-charges.ipynb")
assert research is not None
```

**Cell 27 [markdown]:** `## Section 12: Deployment Pipeline`

**Cell 28 [code]:**
```python
postbacks = PostbackHandler(api_secret=api_secret)
deployment = DeploymentPipeline(
    broker=broker,
    feed=feed,
    postbacks=postbacks,
    instrument_tokens=[infy_token],
)
print(f"DeploymentPipeline: {deployment}")
print("Full start requires a live Strategy — see notebooks/10-deployment-pipeline.ipynb")
assert deployment is not None
```

**Cell 29 [markdown]:** `## Summary`

**Cell 30 [code] — Pass/fail table:**
```python
results = [
    ("1. Login & Auth",          record is not None and not record.is_expired()),
    ("2. Instruments",           len(nse_instruments) > 0),
    ("3. Historical OHLCV",      len(candles) > 0),
    ("4. NSE Calendar",          cal is not None),
    ("5. Backtest + Charges",    charges.total >= 0),
    ("6. Lot Sizing",            round_to_lot(76, 50) == 100),
    ("7. Option Chain",          atm is not None),
    ("8. Greeks",                greeks.delta is not None),
    ("9. Live Broker",           True),
    ("10. Ticker Feed",          connected),
    ("11. Research Pipeline",    research is not None),
    ("12. Deployment Pipeline",  deployment is not None),
]
print(f"{'Section':<32} Status")
print("-" * 42)
for name, ok in results:
    print(f"{name:<32} {'PASS' if ok else 'FAIL'}")
all_passed = all(ok for _, ok in results)
print(f"\nOverall: {'ALL PASS' if all_passed else 'SOME SECTIONS FAILED'}")
```

- [ ] **Step 2: Verify the notebook is valid JSON**

```bash
python -c "
import json, sys
with open('notebooks/11-full-live-integration.ipynb') as f:
    nb = json.load(f)
cells = nb.get('cells', [])
print(f'Valid notebook: {len(cells)} cells, nbformat={nb.get(\"nbformat\")}.{nb.get(\"nbformat_minor\")}')
"
```

Expected: `Valid notebook: 30 cells, nbformat=4.X`

- [ ] **Step 3: Commit**

```bash
git add notebooks/11-full-live-integration.ipynb
git commit -m "notebooks: add 11-full-live-integration.ipynb — 12-section library tour with getpass auth"
```

---

### Task 6: Integration testing guide

**Files:**
- Create: `docs/integration-testing.md`

- [ ] **Step 1: Write `docs/integration-testing.md`**

```markdown
# Integration Testing

Unit tests use fake clients and run in CI with no credentials.
Integration tests hit the **live Kite Connect API** and run **locally or on a VPS only — never in CI**.

## Prerequisites

1. An active [Kite Connect](https://kite.trade) subscription.
2. Your app registered at https://developers.kite.trade (any redirect URI works; `http://127.0.0.1` is fine).
3. `ml4t-india[dev]` installed: `pip install --pre -e '.[dev]'`

## First-time setup

```bash
python scripts/store_kite_credentials.py
```

The script:
1. Prompts for your **API key** and **API secret** (stored in OS keychain, not on disk).
2. Prints your personalised login URL — open it in a browser.
3. After login, Kite redirects you to your app URL with `?request_token=XXX` in the query string.
4. Paste that token when prompted; the script exchanges it for an **access token** and stores it.

**Where credentials are stored:**
- Windows: Windows Credential Manager (search for "ml4t-india")
- Linux: GNOME Keyring / libsecret
- macOS: macOS Keychain Access

Nothing is written to any file in or near the repository.

## Daily token refresh

Kite access tokens expire at ~06:00 IST every morning. Run this before each test session:

```bash
python scripts/store_kite_credentials.py --refresh
```

Prompts only for the new request token; API key and secret are reused from the keychain.

## Verify credentials are stored

```bash
python scripts/store_kite_credentials.py --verify
```

Shows masked values (`abc****xyz`) for all four entries.

## Running integration tests

```bash
pytest -m integration -v
```

Tests auto-skip with a clear message if any credential is absent:
```
SKIPPED: Kite credentials not found in keychain — run: python scripts/store_kite_credentials.py
```

### What the smoke test does

1. Connects to Kite Connect and validates the access token.
2. Fetches available cash (equity segment).
3. Fetches total account value (cash + open position MTM).
4. Lists all open positions.
5. Places a **₹1 LIMIT BUY for 1 share of INFY** — far off-market, zero fill risk.
6. Confirms the order appears in pending orders.
7. Cancels the order and verifies acceptance.
8. Disconnects.

## VPS / Linux headless setup

Install the libsecret backend:

```bash
sudo apt install libsecret-tools gnome-keyring dbus-x11
pip install keyring secretstorage jeepney
```

For headless environments (no display), start a D-Bus session manually:

```bash
eval $(dbus-launch --sh-syntax)
gnome-keyring-daemon --start --components=secrets
export $(gnome-keyring-daemon --start --components=secrets)
```

Then run `python scripts/store_kite_credentials.py` as normal.

## Clearing credentials

```bash
python scripts/store_kite_credentials.py --clear
```

Deletes all four keychain entries. Run again after rotating API keys.

## Adding other brokers

To add integration tests for Upstox, AngelOne, or 5paisa:

1. Add new keychain keys to `scripts/store_kite_credentials.py` (e.g. `upstox_api_key`).
2. Add a new `@pytest.fixture(scope="session")` in `tests/integration/conftest.py`.
3. Add `tests/integration/test_<broker>_smoke.py`.

Follow the same auto-skip pattern as the Kite fixture.
```

- [ ] **Step 2: Commit**

```bash
git add docs/integration-testing.md
git commit -m "docs: integration-testing.md — credential setup, daily refresh, VPS guide"
```

---

### Task 7: Release workflow guide

**Files:**
- Create: `docs/releasing.md`

- [ ] **Step 1: Write `docs/releasing.md`**

```markdown
# Release Workflow

## PyPI (automatic on version tag push)

### One-time setup: PyPI Trusted Publisher

Do this once before the first release at [pypi.org](https://pypi.org):

1. Log in → your project → **Publishing** tab → **Add a new publisher**
2. Publisher: **GitHub Actions**
3. Repository owner: `shankarpandala`
4. Repository name: `ml4t-india`
5. Workflow filename: `publish.yml`
6. Environment name: `pypi`

No `PYPI_API_TOKEN` secret is needed — OIDC handles authentication.

### Release steps

```bash
# 1. Update CHANGELOG.md — move [Unreleased] to a dated release
#    ## [0.1.0] - 2026-04-29
#    ### Added
#    - ...

# 2. Commit and tag
git add CHANGELOG.md
git commit -m "chore: release v0.1.0"
git tag v0.1.0
git push origin main --tags
```

GitHub Actions fires `.github/workflows/publish.yml` automatically:
- Builds wheel + sdist (`python -m build`)
- Publishes to PyPI via OIDC Trusted Publisher

Verify at: https://pypi.org/project/ml4t-india/

## conda-forge

### First release (manual PR)

1. Build the sdist and compute its SHA256:
   ```bash
   python -m build --sdist
   sha256sum dist/ml4t_india-0.1.0.tar.gz
   ```

2. Update `conda-recipe/meta.yaml`:
   - Set `version` to `0.1.0`
   - Replace `PLACEHOLDER_SHA256` with the actual SHA256

3. Check that all `run` dependencies exist on conda-forge:
   ```bash
   # For each dependency:
   curl -s https://anaconda.org/conda-forge/<package>/files | grep -c "tar.bz2"
   ```
   > **Note:** The `ml4t-*` packages (`ml4t-data`, `ml4t-engineer`, etc.) must be on conda-forge
   > before `ml4t-india` can be submitted. If they are not, submit those recipes first.

4. Fork [conda-forge/staged-recipes](https://github.com/conda-forge/staged-recipes).

5. Copy `conda-recipe/meta.yaml` → `recipes/ml4t-india/meta.yaml` in your fork.

6. Open a PR. The conda-forge bot reviews the recipe; maintainers merge after checks pass
   (typically 1–3 business days).

7. After acceptance, conda-forge creates `conda-forge/ml4t-india-feedstock`.

### Subsequent releases (automatic)

The `regro-cf-autotick-bot` monitors PyPI and opens a PR to the feedstock within 24–48 hours
of each new PyPI release. Review and merge that PR.

## Version numbers

`hatch-vcs` derives the version from git tags. Tag `v0.1.0` → package version `0.1.0`.
No manual editing of version files is needed.
```

- [ ] **Step 2: Commit**

```bash
git add docs/releasing.md
git commit -m "docs: releasing.md — PyPI OIDC + conda-forge workflow"
```

---

### Task 8: Update README.md

**Files:**
- Modify: `README.md`

- [ ] **Step 1: Update the status line**

Find:
```markdown
> **Status:** pre-alpha. Phase-0 scaffolding in progress.
```

Replace with:
```markdown
> **Status:** pre-alpha. Phase 0 + Phase 1 complete — Kite, Upstox, AngelOne, and 5paisa broker
> adapters, option chain, Greeks, NSE calendar, backtest charges, research and deployment pipelines
> are all implemented and tested.
```

- [ ] **Step 2: Add Integration Testing and Security sections after `## Installation`**

Insert after the closing code block of the Installation section:

```markdown
## Integration Testing

Real-broker tests against live Kite Connect run **locally only** — never in CI.
Credentials are stored in the OS keychain (Windows Credential Manager / libsecret).

```bash
# First time: store API key, secret, and access token in OS keychain
python scripts/store_kite_credentials.py

# Daily: refresh the access token (Kite rotates tokens at ~06:00 IST)
python scripts/store_kite_credentials.py --refresh

# Run the smoke test
pytest -m integration -v
```

See [docs/integration-testing.md](docs/integration-testing.md) for the full guide,
including VPS / Linux setup and adding tests for other brokers.

## Security

- API keys and access tokens are stored **exclusively in the OS keychain** via `keyring`.
- No credential files exist anywhere in or adjacent to the repository.
- Integration tests skip automatically when credentials are absent — CI never sees them.
- The publish workflow uses PyPI OIDC Trusted Publishers — no stored API token secrets.
```

- [ ] **Step 3: Commit**

```bash
git add README.md
git commit -m "docs(readme): update status; add Integration Testing and Security sections"
```

---

### Task 9: Update AGENTS.md

**Files:**
- Modify: `AGENTS.md`

- [ ] **Step 1: Replace the Testing section**

Find and replace the entire Testing section:

```markdown
## Testing

- Unit tests: pure, fast, fake-driven (`FakeKiteClient`).
- Contract tests: verify our classes substitute for upstream protocols.
- Cassette tests: recorded HTTP (VCR / respx) &mdash; no network in CI.
- Integration tests: opt-in via `KITE_SANDBOX=1`, nightly job only.
- Snapshot tests: assert the upstream API shape we depend on.

See `docs/` (once Phase-0 completes) for the full contributor guide.
```

Replace with:

```markdown
## Testing

- **Unit tests** — pure, fast, fake-driven (`FakeKiteClient`, `FakeUpstoxClient`, etc.). Run in CI on every push/PR.
- **Contract tests** — verify our classes substitute for upstream protocols.
- **Cassette tests** — recorded HTTP (VCR / respx); no network in CI.
- **Property tests** — Hypothesis tests for math-heavy modules (charges, Greeks, lot sizing).
- **Integration tests** — real Kite Connect API; **local/VPS only, never in CI**.
- **Snapshot tests** — assert the upstream API shape we depend on.

### Running integration tests

```bash
# First time
python scripts/store_kite_credentials.py

# Daily (Kite rotates tokens at ~06:00 IST)
python scripts/store_kite_credentials.py --refresh

# Run
pytest -m integration -v
```

Tests auto-skip when credentials are absent — no failures in CI. See `docs/integration-testing.md`.

### Adding integration tests for other brokers

Follow the Kite pattern:
1. Add new keychain keys (e.g. `upstox_api_key`) to `scripts/store_kite_credentials.py`.
2. Add a session-scoped fixture in `tests/integration/conftest.py`.
3. Create `tests/integration/test_<broker>_smoke.py` with `@pytest.mark.integration`.
```

- [ ] **Step 2: Commit**

```bash
git add AGENTS.md
git commit -m "docs(agents): expand Testing section with integration test instructions"
```

---

### Task 10: Update docs/quickstart.md

**Files:**
- Modify: `docs/quickstart.md`

- [ ] **Step 1: Append a new section at the end of the file**

Add after the last section ("7 — Session awareness"):

```markdown
## 8 &mdash; Real broker connection (integration testing)

Before connecting to a live broker, store your Kite credentials in the OS keychain:

```bash
python scripts/store_kite_credentials.py
```

Then run the integration smoke test to verify your full broker connection:

```bash
pytest -m integration -v
```

Or run the comprehensive tour notebook, which prompts for credentials via `getpass`
and exercises every library feature end-to-end:

```bash
jupyter notebook notebooks/11-full-live-integration.ipynb
```

See [integration-testing.md](integration-testing.md) for the full guide including
VPS setup, daily token refresh, and adding other brokers.
```

- [ ] **Step 2: Commit**

```bash
git add docs/quickstart.md
git commit -m "docs(quickstart): add Section 8 — Real broker connection"
```

---

### Task 11: Update CHANGELOG.md

**Files:**
- Modify: `CHANGELOG.md`

- [ ] **Step 1: Replace the entire file content**

```markdown
# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/)
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- Integration test infrastructure: OS-keychain credential management via `keyring`,
  `scripts/store_kite_credentials.py` (first-time setup, `--refresh`, `--verify`, `--clear`),
  `tests/integration/` package with session-scoped auto-skip fixtures.
- `tests/integration/test_kite_smoke.py`: real-broker smoke test for `KiteBroker`
  (connect → cash → account value → positions → place ₹1 LIMIT → confirm pending → cancel → disconnect).
- `notebooks/11-full-live-integration.ipynb`: 12-section tour notebook covering every
  library feature with interactive `getpass` credential input.
- `docs/integration-testing.md`: credential setup, daily token refresh, VPS/Linux guide.
- `docs/releasing.md`: tag → PyPI OIDC Trusted Publisher + conda-forge workflow.
- `.github/workflows/publish.yml`: tag-triggered automatic publish via PyPI Trusted Publisher.
- `conda-recipe/meta.yaml`: conda-forge recipe template for initial staged-recipes submission.

---

## [Phase 1 — Broker Adapters, Options, Workflows] — 2026-04-29

*Note: Phase 1 was developed and merged incrementally; this entry documents the full scope.*

### Added

- **`KiteBroker`** — Zerodha Kite implementation of `IndianBrokerBase`: full order lifecycle
  (regular / AMO / CO / iceberg), position management, account queries, product mapping
  (CNC / MIS / NRML / MTF). 25 unit tests via `FakeKiteClient`.
- **`KiteTickerFeed`** — binary WebSocket tick feed for up to 3 000 instruments per connection,
  ltp / quote / full modes, reconnect-safe subscription replay. 16 unit tests via `FakeTicker`.
- **`PostbackHandler`** — HMAC-authenticated Kite order-state webhook handler. 21 unit tests.
- **`UpstoxBroker`** — Upstox SDK adapter implementing `IndianBrokerBase` via `UpstoxClientProtocol`.
  15 unit tests via `FakeUpstoxClient`.
- **`AngelOneBroker`** — Angel One SmartAPI adapter via `AngelClientProtocol`. 15 unit tests.
- **`FivePaisaBroker`** — 5paisa SDK adapter via `FivePaisaClientProtocol`. 10 unit tests.
- **`OptionChain`** — option chain analytics: PCR, max-pain strike, ATM ladder, near-ATM slices.
- **`compute_greeks`** — Black-Scholes Greeks (delta, gamma, theta, vega) with `py_vollib`
  acceleration and pure-Python `math.erf` fallback. 34 tests.
- **`NSECalendar`** — NSE/BSE market session calendar via `pandas-market-calendars`.
- **`ZerodhaChargesModel`** — exact Zerodha fee schedule: brokerage, STT, exchange turnover fee,
  GST, SEBI fee, stamp duty for all segments (equity delivery / intraday, F&O, currency, commodity).
- **`round_to_lot` / `floor_to_lot`** — F&O lot-size rounding with strict validation.
- **`ResearchPipeline`** — facade composing `KiteProvider` + backtest engine with `nse_india` preset.
- **`DeploymentPipeline`** — facade composing `KiteBroker` + `KiteTickerFeed` + `PostbackHandler`.
- Example notebooks 01–10: login, instruments, OHLCV, backtest + charges, lot sizing,
  option chain, Greeks sensitivity, live broker orders, ticker feed, deployment pipeline.
- Hypothesis property tests for charges, Greeks, and lot sizing.
- CLI entry point `ml4t-india` with `login` and `whoami` sub-commands.
- Full CI matrix: Python 3.12 / 3.13 mainline; 3.14 / 3.13t / 3.14t experimental.
- Weekly `upstream-drift.yml` cron: installs latest `ml4t-*` from PyPI and re-runs suite.

---

## [Phase 0 — Scaffolding] — 2026-03-01

### Added

- `pyproject.toml` with hatch-vcs versioning, full dependency declarations, ruff + ty config.
- Package skeleton at `src/ml4t/india/` (namespace package, PEP 420).
- `IndianBrokerBase`, `IndianTickerFeedBase` abstract base classes.
- `IndianOHLCVProvider` abstract base.
- `FakeKiteClient` in-memory test double.
- `IndiaError` exception hierarchy.
- `tests/contracts/test_upstream_api_snapshot.py` — upstream API drift guard.
- CI matrix (Python 3.12 / 3.13 / 3.14 / 3.13t / 3.14t) via `workflow_dispatch`.
- `upstream-drift.yml` weekly cron.
- MkDocs documentation skeleton.
- Pre-commit configuration (`ruff`, `ty`).
```

- [ ] **Step 2: Commit**

```bash
git add CHANGELOG.md
git commit -m "docs(changelog): Phase-0, Phase-1, and [Unreleased] integration+publish work"
```

---

### Task 12: PyPI publish workflow

**Files:**
- Create: `.github/workflows/publish.yml`

- [ ] **Step 1: Create the workflow file**

```yaml
name: Publish

on:
  push:
    tags:
      - 'v*'   # fires on v0.1.0, v1.2.3, etc.

permissions:
  contents: read
  id-token: write   # required for OIDC Trusted Publisher

jobs:
  build:
    name: Build distribution
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
        with:
          fetch-depth: 0   # hatch-vcs reads the tag from full history

      - uses: actions/setup-python@v5
        with:
          python-version: '3.12'

      - name: Install build backend
        run: python -m pip install --upgrade pip build

      - name: Build wheel and sdist
        run: python -m build

      - name: Upload dist artifacts
        uses: actions/upload-artifact@v4
        with:
          name: dist
          path: dist/
          if-no-files-found: error

  publish-pypi:
    name: Publish to PyPI
    needs: build
    runs-on: ubuntu-latest
    environment:
      name: pypi
      url: https://pypi.org/project/ml4t-india/
    permissions:
      id-token: write   # OIDC — no PYPI_API_TOKEN secret needed
    steps:
      - name: Download dist artifacts
        uses: actions/download-artifact@v4
        with:
          name: dist
          path: dist/

      - name: Publish to PyPI
        uses: pypa/gh-action-pypi-publish@release/v1

  validate-conda-recipe:
    name: Validate conda recipe
    needs: build
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4

      - name: Install conda-build
        run: pip install conda-build

      - name: Lint conda recipe
        run: conda-build --check conda-recipe/

      - name: Instructions for conda-forge
        run: |
          echo "::notice::First release: open a PR to conda-forge/staged-recipes with conda-recipe/meta.yaml"
          echo "::notice::See docs/releasing.md for the step-by-step conda-forge submission guide."
          echo "::notice::After first acceptance, the regro-cf-autotick-bot handles future releases automatically."
```

- [ ] **Step 2: Validate the YAML is syntactically correct**

```bash
python -c "import yaml; yaml.safe_load(open('.github/workflows/publish.yml'))" && echo "Valid YAML"
```

Expected: `Valid YAML`

- [ ] **Step 3: Commit**

```bash
git add .github/workflows/publish.yml
git commit -m "ci: tag-triggered PyPI OIDC publish + conda recipe validation"
```

---

### Task 13: conda-forge recipe

**Files:**
- Create: `conda-recipe/meta.yaml`

- [ ] **Step 1: Create the directory and recipe**

```bash
mkdir -p conda-recipe
```

Create `conda-recipe/meta.yaml`:

```yaml
# conda-forge recipe for ml4t-india
#
# BEFORE SUBMITTING to conda-forge/staged-recipes:
#   1. Verify all `run` dependencies exist on conda-forge.
#      The ml4t-* packages (ml4t-data, ml4t-engineer, etc.) must be submitted
#      to conda-forge FIRST if they are not already present.
#      Check: https://anaconda.org/conda-forge/<package-name>
#   2. Build the PyPI sdist: python -m build --sdist
#   3. Replace PLACEHOLDER_SHA256 with: sha256sum dist/ml4t_india-VERSION.tar.gz
#   4. Copy this file to recipes/ml4t-india/meta.yaml in a fork of
#      https://github.com/conda-forge/staged-recipes and open a PR.
#
{% set name = "ml4t-india" %}
{% set version = "0.1.0" %}

package:
  name: {{ name|lower }}
  version: {{ version }}

source:
  url: https://pypi.org/packages/source/{{ name[0] }}/{{ name }}/{{ name.replace('-', '_') }}-{{ version }}.tar.gz
  sha256: PLACEHOLDER_SHA256

build:
  number: 0
  noarch: python
  script: {{ PYTHON }} -m pip install . --no-deps --no-build-isolation -vv

requirements:
  host:
    - python >=3.12
    - pip
    - hatchling
    - hatch-vcs
  run:
    - python >=3.12
    # ml4t ecosystem — must be on conda-forge before this recipe can be accepted
    # Uncomment once each is available:
    # - ml4t-data
    # - ml4t-engineer
    # - ml4t-backtest
    # - ml4t-live
    # - ml4t-diagnostic
    - kiteconnect >=5.0.0
    - pandas-market-calendars >=4.3.0
    - polars >=0.20
    - pyarrow >=14
    - pandas >=2
    - httpx >=0.27,<1
    - tenacity >=8
    - pydantic >=2
    - pydantic-settings >=2
    - pyyaml >=6
    - click >=8
    - structlog >=23
    - rich >=13
    - pytz >=2024.1

test:
  imports:
    - ml4t.india
    - ml4t.india.live
    - ml4t.india.options
    - ml4t.india.backtest
    - ml4t.india.calendar
  commands:
    - ml4t-india --help

about:
  home: https://github.com/shankarpandala/ml4t-india
  summary: Algorithmic trading for Indian markets (NSE, BSE) on top of the ml4t-* ecosystem
  description: |
    ml4t-india is a thin extension layer that adapts the five ML4T companion
    libraries to Indian equity and derivatives markets via Zerodha Kite Connect v3.
    It provides broker adapters (Kite, Upstox, AngelOne, 5paisa), option chain
    analytics, Black-Scholes Greeks, NSE calendar, exact Zerodha charges, and
    research + deployment pipeline facades.
  dev_url: https://github.com/shankarpandala/ml4t-india

extra:
  recipe-maintainers:
    - shankarpandala
```

- [ ] **Step 2: Validate YAML syntax**

```bash
python -c "import yaml; yaml.safe_load(open('conda-recipe/meta.yaml'))" && echo "Valid YAML"
```

Expected: `Valid YAML`

- [ ] **Step 3: Commit**

```bash
git add conda-recipe/meta.yaml
git commit -m "ci(conda): conda-forge recipe template for staged-recipes submission"
```

---

## Self-review

### Spec coverage check

| Spec requirement | Task |
|---|---|
| `keyring>=25` in dev deps | Task 1 |
| `scripts/store_kite_credentials.py` with --refresh/--verify/--clear | Task 2 |
| `tests/integration/conftest.py` with auto-skip | Task 3 |
| Full smoke test: connect/account/positions/order round-trip/disconnect | Task 4 |
| 12-section tour notebook with `getpass` | Task 5 |
| `docs/integration-testing.md` with VPS guide | Task 6 |
| `docs/releasing.md` | Task 7 |
| README.md Integration Testing + Security sections + status update | Task 8 |
| AGENTS.md Testing section expanded | Task 9 |
| `docs/quickstart.md` Section 8 | Task 10 |
| CHANGELOG.md Phase-0, Phase-1, [Unreleased] | Task 11 |
| `publish.yml` tag-triggered OIDC | Task 12 |
| `conda-recipe/meta.yaml` | Task 13 |

### Type consistency

- `KiteCredentials` dataclass: defined in `tests/integration/conftest.py`, used as parameter type in `kite_broker` fixture — consistent ✓
- `kite_broker` fixture return type: `KiteBroker` — consistent with test file parameter annotation `kite_broker: KiteBroker` ✓
- `AsyncKiteClient.from_api_key(api_key, access_token)` — confirmed from `src/ml4t/india/kite/client.py:166` ✓
- `KiteBroker(client=client)` where `client` is `AsyncKiteClient` — confirmed from `src/ml4t/india/live/kite_broker.py:187` ✓
- `KiteTickerFeed(api_key=..., access_token=..., default_mode=...)` — confirmed from `src/ml4t/india/live/kite_ticker_feed.py:113` ✓
- `feed.on_connect(callable)`, `feed.on_ticks(callable)`, `feed.subscribe([tokens])`, `await feed.start()`, `feed.stop()` — confirmed from `kite_ticker_feed.py` ✓

### Placeholder scan

- `PLACEHOLDER_SHA256` in `conda-recipe/meta.yaml` — **intentional**: the SHA256 is computed at release time from the built sdist. The recipe includes a comment explaining this. Not a plan failure. ✓
- All other steps contain complete, runnable code. ✓

# Secure Kite Integration Testing + Publishing Readiness

**Date:** 2026-04-29  
**Status:** Approved  
**Scope:** Local-only real-broker smoke tests (Zerodha Kite), OS-keychain credential storage, comprehensive tour notebook, doc updates, and publishing pipeline.

---

## Context

`ml4t-india` has completed Phase 0 + Phase 1. All four broker adapters (Kite, Upstox, AngelOne, 5paisa) are implemented and tested with fake clients. Before cutting the first release, the project needs:

1. Real-broker integration tests that exercise `KiteBroker` against live Kite Connect endpoints — locally only, never in CI.
2. A credential management system that stores broker API keys in the OS keychain (not in any file, not in git).
3. A comprehensive Jupyter notebook that demos every library feature end-to-end with interactive credential input.
4. Documentation updates covering the credential setup, integration test workflow, and release process.
5. A manual-only PyPI publish step added to CI.

---

## Credential Storage Architecture

### Mechanism

Credentials are stored in the **OS keychain** via the `keyring` Python library:
- **Windows (local dev):** Windows Credential Manager
- **Linux (VPS):** GNOME Keyring / libsecret (same `keyring` API, zero code change)

No plaintext credential files exist anywhere in or adjacent to the repo. `.pypirc` is already gitignored.

### Keychain entries

Service name: `ml4t-india`

| `keyring` username | Contents |
|---|---|
| `kite_api_key` | Kite Connect API key |
| `kite_api_secret` | Kite Connect API secret |
| `kite_request_token` | One-time request token from the OAuth redirect |
| `kite_access_token` | Daily session token (generated from request token + secret) |

`kite_access_token` is the value consumed at test time. It must be refreshed daily (Kite sessions expire at end of day). The setup script handles both first-time storage and daily refresh.

---

## Components

### 1. `scripts/store_kite_credentials.py`

A standalone script (no repo imports required — only `keyring` and stdlib). Behaviour:

- Prompts for each credential using `getpass.getpass()` — no terminal echo.
- Calls `keyring.set_password("ml4t-india", username, value)` for each.
- `--clear` flag calls `keyring.delete_password(...)` for all four entries and exits.
- `--verify` flag reads all four back and prints masked values (e.g. `kite_api_key = abc****xyz`) to confirm storage succeeded.
- Idempotent: re-running overwrites previous values.
- Cross-platform: no Windows-specific code; `keyring` handles OS dispatch.

**Usage (first time):**
```
python scripts/store_kite_credentials.py
```

**Refresh access token (daily before running integration tests):**
```
python scripts/store_kite_credentials.py --refresh-token
```
(Prompts only for `kite_request_token` and `kite_access_token`.)

**Verify stored credentials:**
```
python scripts/store_kite_credentials.py --verify
```

**Clear all credentials:**
```
python scripts/store_kite_credentials.py --clear
```

---

### 2. `tests/integration/conftest.py`

Session-scoped fixture `kite_credentials` that:

1. Calls `keyring.get_password("ml4t-india", username)` for all four keys.
2. If any are `None`, calls `pytest.skip("Kite credentials not found in keychain — run scripts/store_kite_credentials.py first")` on the entire session.
3. Returns a typed `KiteCredentials` dataclass with `api_key`, `api_secret`, `request_token`, `access_token`.

A second fixture `kite_broker` depends on `kite_credentials`, builds a real `KiteConnect` session, and yields a connected `KiteBroker`. Teardown calls `broker.disconnect()`.

**Skipping behaviour:** Any developer without credentials stored gets clean skips, not failures. CI never sees these tests (no credentials in GitHub Secrets).

---

### 3. `tests/integration/test_kite_smoke.py`

Marked `@pytest.mark.integration`. Full smoke test sequence:

```
connect()
  → assert is_connected_async() is True

get_account_value_async()
  → assert isinstance(result, float)
  → assert result >= 0

get_cash_async()
  → assert isinstance(result, float)
  → assert result >= 0

get_positions_async()
  → assert isinstance(result, dict)

submit_order_async(
    asset="NSE:INFY",
    quantity=1,
    side=OrderSide.BUY,
    order_type=OrderType.LIMIT,
    limit_price=1.0,      # ₹1 — far off-market, zero fill risk
    product="CNC",
)
  → assert order.order_id is not None
  → assert order.status == OrderStatus.PENDING

get_pending_orders_async()
  → assert any order with matching order_id exists

cancel_order_async(order_id)
  → assert returns True

disconnect()
  → assert is_connected_async() is False
```

All assertions are structural — no price or account-value assertions that would be fragile across different accounts.

**Running integration tests:**
```bash
pytest -m integration -v
```

---

### 4. `notebooks/11-full-live-integration.ipynb`

A single self-contained notebook that exercises every feature of `ml4t-india`. Credentials are requested interactively via `getpass` at the top — no dependency on the keychain, so the notebook works on any machine.

#### Sections

| Section | Features exercised |
|---|---|
| **1. Login & Auth** | `getpass` credential input, `KiteConnect` session, generate access token, profile fetch |
| **2. Instruments** | `InstrumentsCache`, symbol lookup by name/ISIN, F&O contract resolution |
| **3. Historical OHLCV** | `KiteProvider`, all candle intervals (1m → day), Polars DataFrame, date range |
| **4. NSE Calendar** | `NSECalendar`, market open/close times, holiday list, `is_session()` |
| **5. Backtest + Charges** | `IndianChargesModel`, brokerage + STT + GST calculation on a sample trade |
| **6. Lot Sizing** | `IndianLotSizer`, F&O lot size lookup, quantity rounding |
| **7. Option Chain** | `OptionChain` fetch, PCR, max-pain strike, ATM ladder display |
| **8. Greeks** | Black-Scholes delta/gamma/theta/vega for a sample option |
| **9. Live Broker** | `KiteBroker` connect → account value → positions → place LIMIT order at ₹1 → cancel |
| **10. Ticker Feed** | `KiteTickerFeed` subscribe → receive 5 ticks → unsubscribe → disconnect |
| **11. Research Pipeline** | `ResearchPipeline` construct + run end-to-end |
| **12. Deployment Pipeline** | `DeploymentPipeline` construct + run end-to-end |

Each section is self-contained with a markdown header, brief explanation, code cells, and output assertions (asserts that verify the output has the expected shape).

The notebook ends with a **summary cell** that prints a pass/fail table across all sections.

---

### 5. Documentation Updates

#### `README.md`
Add two new sections after "Installation":
- **Integration Testing** — brief description, points to `docs/integration-testing.md`
- **Security** — states credential policy (keychain only, nothing in git)

Update status badge from "pre-alpha. Phase-0 scaffolding in progress" to reflect Phase 1 completion.

#### `AGENTS.md`
Update the Testing section to add:
- Integration test instructions (`pytest -m integration`)
- Credential setup reference
- Explicit statement: "Integration tests never run in CI — local/VPS only"

#### `docs/quickstart.md`
Add a "Real Broker Connection" section covering the credential setup steps.

#### `docs/integration-testing.md` (new file)
Full guide covering:
1. Prerequisites (active Kite Connect subscription, API key)
2. Generating a request token and access token (step-by-step login flow)
3. Running `scripts/store_kite_credentials.py`
4. Running `pytest -m integration`
5. Daily token refresh workflow
6. VPS setup (Linux keyring backend configuration)
7. Troubleshooting (`--verify`, `--clear`, backend selection)

#### `CHANGELOG.md`
Add a proper `[Unreleased]` section documenting all Phase-1 additions:
- All four broker adapters (Kite, Upstox, AngelOne, 5paisa)
- Option chain + Greeks
- NSE Calendar
- Backtest charges + presets
- Research + Deployment pipelines
- 10 example notebooks
- Full test suite (~150+ tests)

---

### 6. Publishing Pipeline

#### `pyproject.toml`
Add `keyring` to `[project.optional-dependencies].dev`.

#### `.github/workflows/ci.yml`
Add a `publish` job:
- Triggered **only** by `workflow_dispatch` (never auto on push/PR)
- Requires the `build` job to pass first
- Uses `pypa/gh-action-pypi-publish` with `PYPI_API_TOKEN` GitHub secret
- No version tag created automatically — tagging is a manual step performed locally before triggering the workflow

#### Release workflow (documented in `docs/integration-testing.md`):
1. Update `CHANGELOG.md` — move `[Unreleased]` to `[x.y.z] - YYYY-MM-DD`
2. `git tag vX.Y.Z && git push origin vX.Y.Z` — hatch-vcs picks up the tag
3. Open GitHub → Actions → CI → Run workflow (manual trigger)
4. After CI passes, trigger the `publish` workflow manually
5. Verify on PyPI

---

## Security Properties

| Property | How achieved |
|---|---|
| No credentials in git | `.env`, `.pypirc`, `*.key` already gitignored; no new credential files created |
| No credentials in CI | Integration tests only run locally; `pytest -m integration` never appears in `ci.yml` |
| No plaintext on disk | All secrets written to OS keychain via `keyring`; no `.env` file needed |
| Accidental order prevention | Smoke test uses LIMIT order at ₹1 (far off-market); structure-only assertions |
| Masked verification | `--verify` flag prints only first 3 + last 3 characters of each credential |

---

## Out of Scope

- Upstox, AngelOne, 5paisa real-broker integration tests (can be added in the same pattern later)
- Automated daily token refresh (Kite access tokens expire at EOD; refresh is a manual step before running tests)
- GitHub Actions integration tests (explicitly excluded by project policy)
- Order execution with real fill intent (smoke test uses ₹1 limit price to guarantee no fill)

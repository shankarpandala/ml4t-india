# Integration Testing

This guide covers everything needed to run the real-broker smoke tests for
`ml4t-india` against a live Zerodha Kite Connect account. Integration tests
are local-only — they never run on GitHub Actions.

## Prerequisites

- **Python 3.10 or later** (3.12+ recommended; matches the CI matrix)
- **Active Kite Connect developer account** — sign up at
  [kite.trade/developers](https://kite.trade/developers) and create an app
  to obtain an `api_key` and `api_secret`
- **ml4t-india installed with dev extras:**

```bash
pip install "ml4t-india[dev]"
```

The `[dev]` extra pulls in `keyring>=25`, `pytest>=8`, `pytest-asyncio>=0.23`,
and the rest of the test toolchain. If you have an editable checkout:

```bash
pip install -e ".[dev]"
```

---

## First-time setup

### Step 1 — Create a Kite Connect app

1. Go to [kite.trade/developers](https://kite.trade/developers) and sign in
   with your Zerodha account.
2. Click **Create new app**.
3. Choose **Connect** as the app type. Give it a name and set the redirect URL
   to anything you control (e.g. `http://127.0.0.1/` — you only need to read
   the URL you land on, not serve it).
4. Note the **API key** and **API secret** shown on the app details page.

### Step 2 — Run the setup script

```bash
python scripts/store_kite_credentials.py
```

The script prompts for credentials using `getpass` (no terminal echo) and
stores them directly in the OS keychain:

```
=== ml4t-india Kite credential setup ===
Credentials go to the OS keychain -- nothing is written to disk or git.

Kite API key:
Kite API secret:

API key and secret stored in keychain.

Open this URL in your browser to log in:
  https://kite.trade/connect/login?api_key=YOUR_KEY&v=3

After login you are redirected to your app URL with ?request_token=XXX in the URL.
Copy that token and paste it below.

Request token (from browser redirect URL):
Generating access token...
Access token stored. Valid until ~06:00 IST tomorrow.

Run: pytest -m integration -v
```

### Step 3 — Get the request token

Open the login URL printed by the script. After you authenticate with
Zerodha, your browser is redirected to the redirect URL you registered. The
URL looks like:

```
http://127.0.0.1/?request_token=AbCdEfGhIjKlMnOpQrStUvWxYz012345&action=login&status=success
```

Copy the value of the `request_token` query parameter and paste it at the
prompt. The script calls `generate_session()` to exchange the one-time
request token for a daily `access_token`, then stores all four keys in the
keychain:

| Keychain key | What it holds |
|---|---|
| `kite_api_key` | Your app's API key |
| `kite_api_secret` | Your app's API secret |
| `kite_request_token` | The one-time token from the OAuth redirect (retained for audit) |
| `kite_access_token` | The daily session token consumed by the tests |

The service name in the keychain is `ml4t-india`.

> **Important:** `kite_request_token` is single-use. The exchange for
> `access_token` happens once during setup. On subsequent runs only
> `kite_access_token` is read by the tests.

---

## Daily token refresh

Kite rotates session tokens at approximately **06:00 IST** each morning.
Before running tests on a new day, refresh the access token:

```bash
python scripts/store_kite_credentials.py --refresh
```

This skips the API key/secret prompts (they are already stored) and prompts
only for a new `request_token`. Open the Kite login URL, authenticate, copy
the token from the redirect URL, and paste it:

```
=== Daily token refresh ===
Request token (from browser redirect URL):
Generating access token...
Access token stored. Valid until ~06:00 IST tomorrow.

Run: pytest -m integration -v
```

You need to do this once per calendar day before running integration tests.
If you forget, the tests will receive a 401 from Kite — see
[Troubleshooting](#troubleshooting).

---

## Verify stored credentials

To confirm that all four credentials are present without revealing their
values:

```bash
python scripts/store_kite_credentials.py --verify
```

Example output:

```
=== Stored credentials (masked) ===
  kite_api_key:       abc****xyz
  kite_api_secret:    def****uvw
  kite_request_token: ghi****rst
  kite_access_token:  jkl****opq

All credentials stored. Ready to run: pytest -m integration -v
```

The mask shows the first 3 and last 3 characters only. If any entry is
missing the line reads `NOT SET` and a reminder to re-run setup is printed.

---

## Running the integration tests

```bash
pytest tests/integration -m integration -v
```

The `-m integration` marker filter ensures only the smoke tests run. Do not
drop it — without it, pytest collects all tests including unit tests that do
not need live credentials.

### Expected output (live account)

```
collected 6 items

tests/integration/test_kite_smoke.py::TestKiteLiveBroker::test_is_connected PASSED
tests/integration/test_kite_smoke.py::TestKiteLiveBroker::test_get_cash PASSED
tests/integration/test_kite_smoke.py::TestKiteLiveBroker::test_get_account_value PASSED
tests/integration/test_kite_smoke.py::TestKiteLiveBroker::test_get_positions PASSED
tests/integration/test_kite_smoke.py::TestKiteLiveBroker::test_order_roundtrip PASSED
tests/integration/test_kite_smoke.py::TestKiteLiveBroker::test_disconnect PASSED

6 passed in 4.32s
```

### What the order round-trip test does

`test_order_roundtrip` places a LIMIT BUY for 1 share of INFY at Rs 1 — far
below any realistic market price. The order sits in `PENDING` state and is
immediately cancelled. No fill is possible. All assertions are structural
(order ID is non-empty, status equals `PENDING`, asset string matches) and
are never price-based, so they hold regardless of account balance or current
market conditions.

### Expected output (credentials missing)

If credentials are not stored, all tests skip cleanly with no failures:

```
collected 6 items

tests/integration/test_kite_smoke.py::TestKiteLiveBroker::test_is_connected SKIPPED (Kite credentials not found in keychain ...)
...

6 skipped in 0.11s
```

---

## Clear credentials

To remove all four keychain entries (e.g. before handing over a machine or
rotating to a new Kite app):

```bash
python scripts/store_kite_credentials.py --clear
```

Output:

```
Clearing all ml4t-india credentials from keychain...
  Deleted: kite_api_key
  Deleted: kite_api_secret
  Deleted: kite_request_token
  Deleted: kite_access_token
Done.
```

Entries that were already absent are reported as `Not found (already clear)`.

---

## VPS / Linux headless setup

On a headless Linux server (no GUI session), the default `keyring` backend
requires a `dbus` session and GNOME Keyring daemon, which may not be running.
You have two options:

### Option A — `keyrings.cryptfile` (recommended for VPS)

An encrypted file-based keyring that works without a display server:

```bash
pip install keyrings.cryptfile
export PYTHON_KEYRING_BACKEND=keyrings.cryptfile.cryptfile.CryptFileKeyring
```

The first `keyring.set_password()` call prompts you to set a passphrase for
the encrypted file. Subsequent calls require the same passphrase.

Add the export to your shell profile (`~/.bashrc`, `~/.profile`) or to the
systemd service environment if running tests from a scheduled job.

### Option B — `secret-tool` (GNOME Keyring via D-Bus, if available)

If GNOME Keyring is running (common on Ubuntu desktop servers):

```bash
# Confirm the daemon is reachable
secret-tool lookup service ml4t-india account kite_api_key
```

If that returns a value, the default `keyring` backend will work without
any extra configuration.

### Verify the active backend

```python
import keyring
print(type(keyring.get_keyring()))
```

On a VPS with `keyrings.cryptfile` installed and `PYTHON_KEYRING_BACKEND`
set, this prints:
`<class 'keyrings.cryptfile.cryptfile.CryptFileKeyring'>`.

---

## Security properties

| Property | Mechanism |
|---|---|
| API key and secret never touch disk | Stored exclusively in the OS keychain via `keyring`; no `.env` file, no `config.ini`, nothing in git |
| `access_token` never in git | Same — keychain only; `.gitignore` also blocks `*.env`, `*.key`, `.pypirc` |
| On-disk token cache is owner-only | `~/.ml4t/india/token.json` is written with mode `0o600`; its parent directory is `0o700`. Writes are atomic via `os.replace`. |
| No accidental fills | Smoke test uses a LIMIT order at Rs 1 — guaranteed never to fill at any realistic market price |
| Masked verification | `--verify` exposes only the first 3 and last 3 characters of each credential |

### Auditing what is stored

**Windows:** Open **Windows Credential Manager** (search in Start menu),
navigate to **Windows Credentials**, and look for entries under the name
`ml4t-india`.

**Linux (GNOME Keyring):**

```bash
secret-tool search service ml4t-india
```

**macOS:** Open **Keychain Access.app** and search for `ml4t-india`.

### The token cache file

`~/.ml4t/india/token.json` is a local cache written after a successful
`generate_session()` call. It contains:

```json
{
  "api_key": "YOUR_API_KEY",
  "access_token": "DAILY_SESSION_TOKEN",
  "user_id": "AB1234",
  "login_time": "2026-04-29T09:15:00+05:30",
  "ml4t_india_version": "0.1.0"
}
```

This file exists for convenience (the CLI `ml4t-india whoami` reads it). Its
`0o600` mode means other users on the same host cannot read it. To suppress
the file entirely:

```bash
export ML4T_INDIA_TOKEN_PATH=/dev/null
```

The integration tests read credentials from the keychain directly, not from
this file, so suppressing it does not affect test execution.

---

## Troubleshooting

### `keyring.errors.NoKeyringError`

```
keyring.errors.NoKeyringError: No recommended backend was available.
```

Appears on headless Linux when no keyring backend is installed. Fix:

```bash
pip install keyrings.cryptfile
export PYTHON_KEYRING_BACKEND=keyrings.cryptfile.cryptfile.CryptFileKeyring
```

Then re-run `python scripts/store_kite_credentials.py`.

### Expired token — 401 from Kite

If an integration test fails with an `InputException` or `TokenException`
(HTTP 401), the `access_token` has expired (tokens expire at ~06:00 IST):

```bash
python scripts/store_kite_credentials.py --refresh
```

Then rerun the tests.

### Tests are skipped unexpectedly

Verify the keychain has all four entries:

```bash
python scripts/store_kite_credentials.py --verify
```

If any entry shows `NOT SET`, run the full setup (or `--refresh` for the
token keys only). Check that `PYTHON_KEYRING_BACKEND` (if set) points to the
same backend used during setup — a mismatch causes reads to return `None`.

### `PermissionError` on token cache write

The directory `~/.ml4t/india/` could not be created or its permissions could
not be set. Check that your home directory is writable and that no other
process owns the directory with stricter permissions.

---

## CI / CD note

Integration tests are **excluded from GitHub Actions by design**. The `ci.yml`
workflow runs `pytest -ra` without the `-m integration` flag. Because
`pytest.mark.integration` tests are only collected when that marker is
explicitly requested, they are silently omitted from every CI run.

No Kite credentials are stored in GitHub Secrets. No integration test runner
exists in any workflow file. This is intentional: real-broker tests carry
inherent timing sensitivity (market hours, token expiry) and order-submission
risk that make them unsuitable for automated CI pipelines.

Integration tests run:
- Locally on a developer machine after running `store_kite_credentials.py`
- On a dedicated VPS with `keyrings.cryptfile` configured and credentials
  pre-stored

They do not run on GitHub Actions, ever.

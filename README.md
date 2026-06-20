# ml4t-india

[![Python 3.12+](https://img.shields.io/badge/python-3.12+-blue.svg)](https://www.python.org/downloads/)
[![Free-threaded: experimental](https://img.shields.io/badge/free--threaded-experimental-orange.svg)](docs/free-threaded.md)

Algorithmic trading for Indian markets (NSE, BSE) on top of the
[ML4T library ecosystem](https://ml4trading.io/libraries/).

> **Status:** pre-alpha. Phase-1 Zerodha Kite integration complete. Publishing pipeline ready.

## What this is

`ml4t-india` is a thin extension layer that adapts the five ML4T companion
libraries to Indian equity and derivatives markets via the
[Zerodha Kite Connect v3](https://kite.trade/docs/connect/v3/) broker API.

It contributes **only what is India-specific**. Every generic capability
(engine, indicators, diagnostics, risk framework, storage) is delegated to
the upstream libraries unchanged:

| Upstream library | Role | India-specific work |
| ---------------- | ---- | ------------------- |
| [`ml4t-data`](https://github.com/shankarpandala/data) | `DataManager`, `BaseProvider`, storage | `KiteProvider`, bhavcopy providers |
| [`ml4t-engineer`](https://github.com/shankarpandala/engineer) | 120 indicators, labeling, alt bars | None &mdash; consumed as-is |
| [`ml4t-backtest`](https://github.com/shankarpandala/backtest) | Event-driven engine, `Strategy`, presets | `IndianChargesModel`, `nse_india` preset |
| [`ml4t-live`](https://github.com/shankarpandala/live) | `LiveEngine`, `SafeBroker`, protocols | `KiteBroker`, `KiteTickerFeed` |
| [`ml4t-diagnostic`](https://github.com/shankarpandala/diagnostic) | DSR, CPCV, tear sheets | Calendar wiring only |

## Design principles

- **Extend, don't re-implement.** Every adapter subclasses the upstream
  concrete base where one exists (e.g. `IndianOHLCVProvider(BaseProvider)`).
  Where upstream only exposes a `typing.Protocol`, we implement it once in
  an India-level abstract base (`IndianBrokerBase`, `IndianTickerFeedBase`)
  and every concrete broker extends that &mdash; so the protocol is adhered
  to exactly once.
- **Drift-insulated.** The weekly `upstream-drift` CI job installs the
  latest `ml4t-*` from PyPI and re-runs the full suite. Signature-level
  drift is caught by dedicated snapshot tests.
- **Pure Python.** No C extensions of our own, so a single universal wheel
  serves both GIL and free-threaded CPython. See `docs/free-threaded.md`.
- **TDD at every adapter boundary.** Contract tests verify substitutability
  for upstream protocols; recorded HTTP cassettes drive integration.

## Phase-1 scope (Zerodha, full surface)

- Historical candles (1m, 3m, 5m, 10m, 15m, 30m, 60m, day), OI, continuous F&O.
- `KiteTicker` WebSocket (ltp / quote / full modes, 3000 instruments / connection, 3 connections).
- Orders: regular / AMO / CO / iceberg / auction.
- Product types: CNC / MIS / NRML / MTF.
- Option chain with Greeks (Black-Scholes) and analytics (PCR, max-pain, ATM ladder).
- Zerodha fee schedule: brokerage + STT + exchange turnover + GST + SEBI + stamp.
- Bhavcopy bulk providers: NSE / BSE / MCX for long-history backfill.

## Installation

```bash
pip install ml4t-india              # core
pip install ml4t-india[options]     # + Black-Scholes Greeks
pip install ml4t-india[viz]         # + plotly tear sheets
pip install ml4t-india[auto-login]  # + OPT-IN headless login (see warning below)
pip install ml4t-india[all]
```

## Login

The default login is a **manual** browser paste flow — no credentials ever
leave your machine:

```bash
ml4t-india login --api-key $KITE_API_KEY --api-secret $KITE_API_SECRET
# opens a URL; paste back the request_token from the redirect
```

### OPT-IN automated login (`--method auto`)

> [!WARNING]
> **This is strictly weaker security than manual login and is OFF by default.**
> Automated login stores your Kite **password** *and* your **TOTP seed**
> together in the OS keychain. That co-locates both two-factor-authentication
> factors on a single host, defeating the two-party 2FA split that makes TOTP
> valuable. Only enable it on a machine you fully control. Secrets live in the
> **OS keychain only** — never in `.env`, the repo, config files, CI, or logs.
> Your Kite Connect app must have a **`redirect_url` configured** so the
> headless flow can capture the `request_token`.

```bash
# 1. Install the opt-in extra
pip install 'ml4t-india[auto-login]'

# 2. Store API key/secret (if not already) and the auto-login secrets.
#    Every value is captured via getpass and written ONLY to the OS keychain.
python scripts/store_kite_credentials.py             # api_key + api_secret
python scripts/store_kite_credentials.py --auto-setup # user_id + password + TOTP seed

# 3. Log in headlessly (password + TOTP read from the keychain)
ml4t-india login --method auto
```

Inspect what is stored (masked) with `--verify`, or wipe everything with
`--clear`:

```bash
python scripts/store_kite_credentials.py --verify
python scripts/store_kite_credentials.py --clear
```

## Documentation

- [Quickstart](docs/quickstart.md) — login, historical data, backtesting,
  option chain, live trading, NSE calendar
- [End-to-end workflow](docs/end-to-end.md) — `examples/end_to_end.py` runs
  the whole feature set on **real Kite data**, with **paper-simulated order
  execution** (no live orders ever sent)
- [Integration Testing](docs/integration-testing.md) — real broker smoke
  tests with OS keychain credential storage (no secrets in git)
- [Releasing](docs/releasing.md) — tag-triggered PyPI publish via OIDC +
  conda-forge recipe workflow

Full documentation will be published to
[https://shankarpandala.github.io/ml4t-india/](https://shankarpandala.github.io/ml4t-india/)
once Phase-1 stabilises.

## Integration Testing

Real broker tests run locally against a live Zerodha Kite account. Credentials
are stored in the OS keychain — never in `.env` files, config files, or git.

```bash
# Store credentials once (Windows Credential Manager / GNOME Keyring / macOS Keychain)
python scripts/store_kite_credentials.py

# Daily refresh (Kite tokens expire at ~06:00 IST)
python scripts/store_kite_credentials.py --refresh

# Run the 6-test smoke suite
pytest tests/integration -m integration -v
```

Tests skip cleanly when credentials are absent. They never run on GitHub
Actions. See [docs/integration-testing.md](docs/integration-testing.md) for
VPS/Linux setup, troubleshooting, and security properties.

## Secret scanning

This repo handles live Zerodha Kite credentials, so it guards against
committing secrets in three layers:

- **`.gitignore`** keeps token files (`token.json`, `.ml4t/`) out of git.
- **`nbstripout`** (pre-commit) strips notebook **outputs**, where pasted
  tokens / PII tend to land.
- **`gitleaks`** scans **source** — including notebook source cells and any
  arbitrarily-named token file — which the two layers above structurally
  cannot cover. It runs both locally (pre-commit) and in CI
  ([`.github/workflows/gitleaks.yml`](.github/workflows/gitleaks.yml)) on
  every push and pull request, so a leak is caught before merge even if a
  contributor skips the local hook.

Enable the local hooks once:

```bash
uv pip install pre-commit
pre-commit install
```

Run the secret scan manually against the whole tree:

```bash
pre-commit run gitleaks --all-files
# or, with the binary directly:
gitleaks dir . --no-banner
```

**Handling a finding.** If gitleaks flags a line:

1. If it is a **real secret**, do **not** just delete the line — the secret
   is already in your local history. Rotate/revoke it at the source
   (e.g. regenerate the Kite API secret), then remove it from the working
   tree before committing.
2. If it is a **false positive** (a documented placeholder or test fixture),
   add a narrow, exact-string allowlist entry to
   [`.gitleaks.toml`](.gitleaks.toml) with a comment explaining why it is
   safe. Never broaden the allowlist to a pattern that could hide a real
   secret.

## License

Not licensed yet.

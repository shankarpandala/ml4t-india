# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/)
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- **Secure integration testing** — OS keychain credential storage via
  `keyring>=25`; `scripts/store_kite_credentials.py` for first-time setup
  and daily token refresh; `tests/integration/` with 6-test Kite smoke suite
  (connect, account, positions, order round-trip, disconnect); tests skip
  cleanly when credentials are absent.
- **Comprehensive tour notebook** — `notebooks/11-full-live-integration.ipynb`
  covering all 12 library feature areas with `getpass`-based credential input.
- **PyPI publish pipeline** — `.github/workflows/publish.yml` tag-triggered
  (`v*`) via OIDC Trusted Publishers; no stored API tokens.
- **conda-forge recipe** — `conda-recipe/meta.yaml` template for staged-recipes
  PR and local `conda-build` testing.
- **Documentation** — `docs/integration-testing.md` (credential setup, VPS
  guide, security properties, troubleshooting) and `docs/releasing.md`
  (tag → PyPI OIDC + conda-forge workflow with release checklist).

## [Phase-1] — 2026-04-29

Phase-1 delivers the full Zerodha Kite Connect surface area.

### Added

- `KiteClient` / `AsyncKiteClient` — throttled, error-translated facade over
  `kiteconnect.KiteConnect` with token-bucket rate limiting per endpoint
  category (quote / historical / orders / other).
- `KiteRateLimiter` — token-bucket implementation respecting Kite's per-second
  and per-minute limits.
- `KiteBroker` — async broker adapter implementing `IndianBrokerBase`:
  connect/disconnect, cash, account value, positions, submit/cancel orders,
  order round-trip with status mapping.
- `KiteTickerFeed` — WebSocket tick feed (ltp / quote / full modes) with
  `on_connect`, `on_ticks`, `on_close` callbacks; `subscribe()` and `start()`
  / `stop()` lifecycle.
- `KiteProvider` — historical OHLCV data provider for 1m–day intervals with
  `InstrumentsCache` resolution.
- `InstrumentsCache` — lazy-loaded instrument master with resolve by symbol,
  lot size lookup, and F&O filtering.
- `OptionChain` — ATM strike, ±N strikes around ATM, call/put separation.
- `compute_greeks()` — Black-Scholes delta, gamma, theta, vega, rho; `py_vollib`
  if installed, closed-form `math.erf` fallback otherwise.
- `ZerodhaChargesModel` — exact fee calculation: brokerage cap, STT (sell-side
  asymmetry), SEBI, exchange turnover, GST, stamp duty per segment.
- `round_to_lot()` / `floor_to_lot()` — F&O lot-size rounding helpers.
- `nse_calendar()` — NSE trading calendar with `is_session_day()`,
  `next_session()`, `session_bounds()` (IST-aware datetimes).
- `ResearchPipeline` / `DeploymentPipeline` — high-level facades composing
  data, backtest, and live components.
- CLI: `ml4t-india login`, `ml4t-india whoami`.
- Auth: `login_url()`, `generate_session()`, `save_token()`, `load_token()`,
  `TokenRecord.is_expired()` — atomic on-disk token cache with `0o600` mode.
- Kite error taxonomy: `translate()` mapping all `kiteconnect.exceptions.*`
  to `IndiaError` subclasses.
- `FakeKiteClient` — in-memory test double for unit and contract tests.

## [Phase-0] — 2026-03-01

Initial project scaffolding.

### Added

- `pyproject.toml` with `hatch-vcs` versioning, CI matrix across Python
  3.12 / 3.13 / 3.13t (free-threaded), `ruff` linting, `pytest` with
  `asyncio_mode = "auto"` and `integration` marker.
- Minimal package skeleton at `src/ml4t/india/` — `core/` primitives
  (IST constant, enums, exceptions, symbols), namespace package wiring.
- `.github/workflows/ci.yml` — lint + test matrix + build artifact job.

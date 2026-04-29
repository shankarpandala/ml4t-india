# AGENTS.md

Navigation notes for AI agents (and humans) working in this repository.

## Mental model

`ml4t-india` is an **extension layer**, not a standalone trading framework.
Every new capability should first be evaluated against:

1. Does the upstream `ml4t-*` library already provide it? If yes, consume it,
   do **not** re-implement.
2. Is there a concrete base class upstream to extend? If yes, extend it and
   override only the narrowest method(s) needed.
3. Does upstream only expose a `typing.Protocol`? Then implement the protocol
   **once** in the India abstract base (`IndianBrokerBase`, etc.) and have
   broker-specific classes extend that base.
4. Only if none of the above apply, build a new class from scratch. Document
   why upstream could not be reused.

## Layout

```
src/ml4t/india/
  core/     # India primitives: IST, calendars, enums, exceptions, symbols
  kite/     # the ONLY module allowed to import `kiteconnect`
  data/     # providers extending ml4t.data.providers.base.BaseProvider
  backtest/ # charges + preset extensions to ml4t.backtest
  live/     # broker + feed extensions (implement ml4t.live.protocols.*)
  options/  # NEW feature: option chain + Greeks (no upstream equivalent)
  diagnostic/ # thin calendar-aware wrappers
  workflows/  # facades composing the above
  cli/        # click-based entry points
```

## Hard rules

- **Never** import `kiteconnect` outside `src/ml4t/india/kite/`. All broker /
  feed / provider code depends on the `KiteClient` facade, never on the SDK
  directly.
- **Never** reach into `ml4t.*._private` modules. Public API only. If something
  we need is private upstream, raise an issue upstream instead of copying.
- **Never** hard-pin upstream versions. Lower bounds only; drift CI handles
  the rest.
- **Never** add a `license` field to `pyproject.toml` until the project owner
  decides on licensing.

## Conventions

- Polars DataFrames throughout (matching upstream).
- IST (`Asia/Kolkata`) for every timestamp exposed in public API.
- `Decimal` for money when rounding matters (tick-size alignment, charges).
- Async + sync parity: expose both where the broker supports it.
- Log via `structlog`; never print.

## Testing

- Unit tests: pure, fast, fake-driven (`FakeKiteClient`).
- Contract tests: verify our classes substitute for upstream protocols.
- Cassette tests: recorded HTTP (VCR / respx) — no network in CI.
- Integration tests: real Zerodha Kite account, local/VPS only — see
  `docs/integration-testing.md`.
- Snapshot tests: assert the upstream API shape we depend on.

### Running integration tests

Integration tests require real Kite credentials stored in the OS keychain.
They skip cleanly when credentials are absent (no failures on CI).

```bash
# First time: store credentials via OS keychain (Windows Credential Manager,
# GNOME Keyring, or macOS Keychain -- never in files or git)
python scripts/store_kite_credentials.py

# Daily refresh (tokens expire at ~06:00 IST)
python scripts/store_kite_credentials.py --refresh

# Run the smoke suite
pytest tests/integration -m integration -v
```

Integration tests **never run on GitHub Actions**. See
`docs/integration-testing.md` for VPS/Linux setup and troubleshooting.

### FakeKiteClient

`FakeKiteClient` is the in-memory test double for `KiteConnect`. It fits the
`_KiteSDK` structural protocol in `kite/client.py` and is used by all unit
and contract tests. It never touches the network. When writing new unit tests,
inject `FakeKiteClient` instead of a real SDK instance.

See `docs/` for the full contributor guide.

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
- Cassette tests: recorded HTTP (VCR / respx) &mdash; no network in CI.
- Integration tests: opt-in via `KITE_SANDBOX=1`, nightly job only.
- Snapshot tests: assert the upstream API shape we depend on.

See `docs/` (once Phase-0 completes) for the full contributor guide.

# Real-data end-to-end workflow

`examples/end_to_end.py` runs the **entire** ml4t-india feature set against
the **live Zerodha Kite market** in one staged pass, on your own machine.

> **The only thing simulated is order execution.** Every quote, candle, and
> instrument the workflow touches is real Kite data. There is no `--mock`
> flag and no synthetic-data path. Orders are paper-filled against real
> quotes — **no live order is ever sent.**

## Safety guarantee: no live orders

Stage 7 trades through
[`PaperKiteBroker`](../src/ml4t/india/live/paper.py), the library's
paper-execution mode. This is a **structural** guarantee, not a runtime flag:

- The live order path — the only code in this package that calls Kite's
  `place_order` / `place_autoslice_order` — lives in
  `KiteBroker.submit_order_async`. Paper mode uses a **different class**
  (`PaperKiteBroker`) whose order path is pure local arithmetic
  (`simulate_fill`). A `grep` for `.place_order(` in `paper.py` returns
  nothing.
- `PaperKiteBroker` is constructed with a **read-only quote surface**
  (`ReadOnlyQuoteClient`) that forwards only market-data reads
  (`ltp` / `quote` / `ohlc` / `profile` / `positions` / `margins`). It
  exposes no order-mutating method, so the broker holds no object on which
  an order *could* be placed.

The default fill model fills at the real last-traded price, then applies
`ZerodhaChargesModel` for brokerage + statutory charges and an optional
slippage allowance. The fill model is overridable
(`PaperKiteBroker.paper(client, fill_model=...)`).

```python
from ml4t.india.live import PaperKiteBroker, LastPriceFillModel

broker = PaperKiteBroker.paper(
    async_client,                              # real, rate-limited AsyncKiteClient
    fill_model=LastPriceFillModel(slippage_bps=2.0),
    starting_cash=1_000_000.0,
)
await broker.connect()                          # read-only profile() probe
order = await broker.submit_order_async("NSE:RELIANCE", quantity=1)
# order.status is FILLED, priced off the real LTP — nothing was sent to Kite.
```

## One-time setup

1. **Install the extras** you want exercised (base install works too; the
   options and agent stages simply report "skipped" if their extra is
   absent):

   ```bash
   pip install 'ml4t-india[auto-login,options,agent]'
   ```

2. **Store your Kite credentials once** in the OS keychain. They never touch
   disk and are read only by the headless-login path:

   ```bash
   python scripts/store_kite_credentials.py --auto-setup
   ```

   This stores five secrets under the `ml4t-india` keychain service:
   `kite_api_key`, `kite_api_secret`, `kite_user_id`, `kite_password`,
   `kite_totp_secret`.

3. **Configure a `redirect_url`** on your
   [Kite Connect app](https://developers.kite.trade/apps). Any HTTPS URL you
   control works — the headless login harvests the `request_token` from the
   redirect. Without it, login fails with an actionable message.

## Run it

```bash
python examples/end_to_end.py
```

### The daily token cache (login at most once per day)

The first run performs one headless login (password + TOTP) and caches the
session token at `~/.ml4t/india/token.json` (file mode `0600`). Every
subsequent run that day calls `load_token()` and checks
`TokenRecord.is_expired()` — if the cached token is still valid, **no login
happens**. `automated_login()` runs only when the cache is absent or the
token has rotated (Kite rotates daily at ~06:00 IST).

### Stage 8 agent LLM: offline mock (default) or key-free subscription

By default the Stage 8 agent review uses a keyless, deterministic
`MockLLMClient` (empty proposals) — no API key, no subscription, no network.
That keeps the normal run reproducible and dependency-light.

To run the agent against a **real** LLM **without any `ANTHROPIC_API_KEY`**,
opt in with the `ML4T_INDIA_AGENT_LLM` env flag. The `subscription` value
drives the local `claude` CLI over your Claude **subscription** OAuth login
(the CLI's native `--json-schema` constrains the model to the proposals
shape; `ANTHROPIC_API_KEY` is stripped from the subprocess env so the
subscription path is guaranteed):

```bash
ML4T_INDIA_AGENT_LLM=subscription python examples/end_to_end.py
```

**Prerequisite:** the `claude` CLI must be installed and logged in to your
subscription — run `claude`, then `/login`. If it is not logged in, Stage 8
fails loudly with that hint. Leaving the flag unset (or `=mock`) keeps the
offline mock default.

## What each stage exercises

| Stage | Feature(s) exercised |
| ----- | -------------------- |
| 1. Login | `kite.auth`: `load_token` / `TokenRecord.is_expired` / `automated_login` / `save_token` |
| 2. Download | `InstrumentsCache.refresh`/`resolve` + `KiteProvider.fetch_ohlcv` over a `PRESETS` universe, windowed by the NSE calendar |
| 3. Preprocess | `core` enums, `errors.translate`, `round_to_lot`, `ZerodhaChargesModel` |
| 4. Features | `ResearchPipeline(feature_transform=…)` → `ml4t.engineer.compute_features` |
| 5. Strategies | `models.registry.resolve_preset` over the real feature matrix |
| 6. Backtest | `ResearchPipeline.run` on real OHLCV; `OptionChain` + `compute_greeks` options leg where applicable |
| 7. Simulate | `PaperKiteBroker` vs real quotes — **zero live orders** |
| 8. Gate | `IndiaResearchAgent.run` + `deflated_sharpe_ratio` → a printed **ready / not ready** verdict |

The script prints a per-stage progress log and **fails loudly** (via
`errors.translate`) with an actionable message if credentials, the
`redirect_url`, or data are missing.

## Exit codes

| Code | Meaning |
| ---- | ------- |
| 0 | Completed; deploy gate says **READY TO TRADE** |
| 2 | Completed; deploy gate says **NOT READY** (iterate before risking capital) |
| 1 | A stage failed (missing creds / redirect_url / data) — see the printed hint |

## Running the integration test

The same flow is covered by a credential-gated integration test. It **skips**
(never fails) unless both the keychain credentials are present and
`ML4T_INDIA_E2E_REAL=1` is set, so it never runs in CI without credentials:

```bash
ML4T_INDIA_E2E_REAL=1 pytest -m integration tests/integration/test_end_to_end.py
```

The pure paper-fill math is unit-tested separately and runs in CI without any
credentials:

```bash
pytest tests/unit/test_paper_execution.py
```

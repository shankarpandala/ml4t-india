# ml4t-india

Algorithmic trading for Indian markets (NSE, BSE) on top of the
[ML4T library ecosystem](https://ml4trading.io/libraries/).

!!! info "Pre-alpha, live-usable"
    Phases 0&ndash;7 are merged: Kite data + broker + ticker feed,
    option chain + Greeks, charges + lot-size helpers, research +
    deployment pipelines, NSE calendar. Head to the
    [Quickstart](quickstart.md) for end-to-end usage.

## What this library is

`ml4t-india` is a thin extension layer over the five ML4T companion
libraries, specialised for Indian equity and derivatives markets via the
[Zerodha Kite Connect v3](https://kite.trade/docs/connect/v3/) broker
API.

It contributes only India-specific concerns. Everything else &mdash;
engine, indicators, diagnostics, risk framework, storage &mdash; is
delegated upstream unchanged.

| Upstream | Role | India-specific work |
| -------- | ---- | ------------------- |
| [`ml4t-data`](https://github.com/shankarpandala/data) | `DataManager`, `BaseProvider`, storage | `KiteProvider` (Phase 2), bhavcopy providers |
| [`ml4t-engineer`](https://github.com/shankarpandala/engineer) | 120 indicators, labeling, alt bars | None &mdash; consumed as-is |
| [`ml4t-backtest`](https://github.com/shankarpandala/backtest) | Engine, `Strategy`, presets | `IndianChargesModel`, `nse_india` preset (Phase 3) |
| [`ml4t-live`](https://github.com/shankarpandala/live) | `LiveEngine`, `SafeBroker`, protocols | `KiteBroker`, `KiteTickerFeed` (Phase 4) |
| [`ml4t-diagnostic`](https://github.com/shankarpandala/diagnostic) | DSR, CPCV, tear sheets | Calendar wiring only |

## Design principles

* **Extend, don't re-implement.** Every adapter subclasses the upstream
  concrete base where one exists (`IndianOHLCVProvider(BaseProvider)`).
  Where upstream only exposes a `typing.Protocol`, we implement it once
  in an India-level abstract base (`IndianBrokerBase`,
  `IndianTickerFeedBase`) and every concrete broker extends that.
* **Drift-insulated.** A weekly
  [upstream-drift](https://github.com/shankarpandala/ml4t-india/actions/workflows/upstream-drift.yml)
  workflow runs our contract tests against the latest upstream wheels.
  When a signature changes, the drift tests fail with exactly the symbol
  that broke and the line of our code that depends on it.
* **Pure Python.** No C extensions of our own, so a single universal
  wheel serves both GIL and free-threaded CPython. See the Phase-0.2
  commit for the experimental 3.13t / 3.14t CI lanes.
* **TDD at every adapter boundary.** Contract tests verify substitutability
  for upstream protocols; `FakeKiteClient` drives unit-level isolation;
  recorded HTTP cassettes (Phase 3) drive integration.

## What lives here today

| Module | Contents |
| ------ | -------- |
| `ml4t.india.core` | Kite-wire enums (`Exchange`, `Product`, `OrderType`, ...) + `IndiaError` hierarchy |
| `ml4t.india.kite` | `KiteClient` / `AsyncKiteClient` facade, rate limiter, login flow, instruments cache, `FakeKiteClient` |
| `ml4t.india.data` | `IndianOHLCVProvider`, `KiteProvider`, `KiteAsyncProvider` |
| `ml4t.india.backtest` | `IndianChargesModel`, `ZerodhaChargesModel`, `nse_india_config`, `round_to_lot`, `floor_to_lot` |
| `ml4t.india.live` | `IndianBrokerBase`, `IndianTickerFeedBase`, `KiteBroker`, `KiteTickerFeed`, `PostbackHandler` |
| `ml4t.india.options` | `OptionChain`, `compute_greeks` |
| `ml4t.india.calendar` | `NSECalendar` over pandas-market-calendars |
| `ml4t.india.workflows` | `ResearchPipeline`, `DeploymentPipeline` |
| `ml4t.india.cli` | `ml4t-india login` + `whoami` entry points |

See the [Quickstart](quickstart.md) for end-to-end usage and the
[API reference](api.md) for per-symbol documentation.

## Installation

```bash
pip install ml4t-india           # core
pip install ml4t-india[options]  # + Black-Scholes Greeks (Phase 5)
pip install ml4t-india[viz]      # + plotly tear sheets
pip install ml4t-india[all]
```

## Status

Pre-alpha but functionally complete for Zerodha Phase-1 scope
(equity cash + F&amp;O, historical data, live orders, streaming,
option chain, Greeks). Python 3.12 and 3.13 CI lanes are green;
3.14 and 3.13t/3.14t free-threaded lanes are experimental and
tracked separately via [PR #2](https://github.com/shankarpandala/ml4t-india/pull/2).
Phase 8 (hardening, property tests, &gt;=90% coverage) is next.

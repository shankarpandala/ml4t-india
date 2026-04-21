# ml4t-india

Algorithmic trading for Indian markets (NSE, BSE) on top of the
[ML4T library ecosystem](https://ml4trading.io/libraries/).

!!! warning "Pre-alpha"
    This is Phase-0 scaffolding. No Zerodha functionality is wired yet;
    the visible surface today is the abstract-class layer that Phase 1+
    concrete code will extend. See
    [the PR roadmap](https://github.com/shankarpandala/ml4t-india/pull/1)
    for the plan.

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

## What lives here today (Phase-0)

* `ml4t.india.core` &mdash; Kite-wire enums (`Exchange`, `Product`,
  `OrderType`, ...) and the `IndiaError` exception hierarchy.
* `ml4t.india.data` &mdash; `IndianOHLCVProvider` abstract base.
* `ml4t.india.live` &mdash; `IndianBrokerBase`, `IndianTickerFeedBase`
  abstract bases.
* `ml4t.india.kite` &mdash; `FakeKiteClient` test double.

See [Phase-0 Scaffolding](phase-0.md) for details.

## Installation

```bash
pip install ml4t-india           # core
pip install ml4t-india[options]  # + Black-Scholes Greeks (Phase 5)
pip install ml4t-india[viz]      # + plotly tear sheets
pip install ml4t-india[all]
```

## Status

No Zerodha functionality wired yet. Phase 1 adds the `KiteClient` gateway
(rate limiter, retry, circuit breaker, auth flow); Phase 2 adds the real
`KiteProvider`; Phase 4 adds the `KiteBroker`. Track progress on
[PR #1](https://github.com/shankarpandala/ml4t-india/pull/1).

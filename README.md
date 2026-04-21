# ml4t-india

[![Python 3.12+](https://img.shields.io/badge/python-3.12+-blue.svg)](https://www.python.org/downloads/)
[![Free-threaded: experimental](https://img.shields.io/badge/free--threaded-experimental-orange.svg)](docs/free-threaded.md)

Algorithmic trading for Indian markets (NSE, BSE) on top of the
[ML4T library ecosystem](https://ml4trading.io/libraries/).

> **Status:** pre-alpha. Phase-0 scaffolding in progress.

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
pip install ml4t-india           # core
pip install ml4t-india[options]  # + Black-Scholes Greeks
pip install ml4t-india[viz]      # + plotly tear sheets
pip install ml4t-india[all]
```

## Documentation

Full documentation will be published to
[https://shankarpandala.github.io/ml4t-india/](https://shankarpandala.github.io/ml4t-india/)
once Phase-0 completes.

## License

Not licensed yet.

# Ecosystem

`ml4t-india` does not stand alone: it is one leaf of a six-library stack.

```
+-------------------------------------------------------------+
|                       ml4t-india                            |
|  IndianOHLCVProvider  IndianBrokerBase  IndianTickerFeedBase|
|      KiteProvider        KiteBroker       KiteTickerFeed    |
|            IndianChargesModel  (Zerodha charges)            |
+------+-------------+-------------+--------------+-----------+
       |             |             |              |
       v             v             v              v
+------------+ +-------------+ +------------+ +---------------+
| ml4t-data  | | ml4t-       | | ml4t-live  | | ml4t-         |
| DataManager| | backtest    | | LiveEngine | | diagnostic    |
| BaseProv.  | | Engine      | | SafeBroker | | CPCV, DSR     |
| Storage    | | Strategy    | | Protocols  | | Tearsheets    |
+------------+ +-------------+ +------------+ +---------------+
                       ^
                       |
                +------+------+
                | ml4t-       |
                | engineer    |
                | 120 indctrs |
                | Labeling    |
                +-------------+
```

## What each upstream package owns

| Package | Responsibility |
| ------- | -------------- |
| [ml4t-data](https://github.com/shankarpandala/data) | Unified market-data acquisition + storage. 23 global provider adapters, `BaseProvider` template-method class, Hive-partitioned Parquet storage, incremental updates. |
| [ml4t-engineer](https://github.com/shankarpandala/engineer) | Feature engineering. 120 technical indicators, triple-barrier labelling, alternative bars. Consumed by us as-is. |
| [ml4t-backtest](https://github.com/shankarpandala/backtest) | Event-driven backtest engine with exit-first fills, execution / commission / slippage models, framework-parity presets. |
| [ml4t-live](https://github.com/shankarpandala/live) | Live-trading orchestration, `SafeBroker` risk wrapper, shadow-mode `VirtualPortfolio`, broker / feed protocols. |
| [ml4t-diagnostic](https://github.com/shankarpandala/diagnostic) | Statistical validation: DSR, RAS, PBO, CPCV, tearsheets, SHAP-based trade diagnostics. |

## What ml4t-india adds

The India-specific concerns the upstream layer can't (and shouldn't) know:

* IST sessions, NSE / BSE / NFO / BFO / CDS / BCD / MCX calendars.
* Zerodha fee schedule &mdash; STT, GST, SEBI turnover, stamp duty,
  exchange fees, broker brokerage.
* `Exchange`, `Product`, `Variety`, `OrderType` enum values that match
  Zerodha's exact wire strings.
* Lot-size rounding for F&O.
* Bhavcopy bulk providers for long-history backfill without touching
  Kite's historical-data quota.
* Option-chain construction + Greeks (Phase 5).

## Design rule: extend, don't re-implement

Every adapter lives as a narrow subclass of an upstream class (or
upstream `typing.Protocol`, once via an India-layer abstract base).
When upstream adds a method with a default implementation, every
ml4t-india subclass inherits it automatically. When upstream *changes*
a shape we depend on, the weekly drift cron catches it before it
breaks production.

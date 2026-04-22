# Quickstart

This walks through the three Indian-market workflows the library
supports today: **research** (historical data + backtest),
**live trading** (orders + streaming), and **option-chain analytics**.

All examples assume:

```python
from ml4t.india.kite.client import KiteClient, AsyncKiteClient
```

## 1 &mdash; Log in

The CLI handles the OAuth flow and caches a token at
`~/.ml4t/india/kite_token.json`:

```bash
ml4t-india login --api-key YOUR_API_KEY --api-secret YOUR_API_SECRET
# prints a login URL -- open it, paste the request_token when prompted
ml4t-india whoami  # prints redacted cached record + live profile
```

`KiteClient.from_api_key()` can then pick up the cached token:

```python
from ml4t.india.kite.auth import load_token, default_token_path

record = load_token(default_token_path("YOUR_API_KEY"))
client = KiteClient.from_api_key(
    api_key="YOUR_API_KEY",
    access_token=record.access_token,
)
```

## 2 &mdash; Historical data + backtest

```python
import datetime as dt
from ml4t.india.data import KiteProvider
from ml4t.india.kite.instruments import InstrumentsCache
from ml4t.india.workflows import ResearchPipeline
from ml4t.backtest import Strategy

instruments = InstrumentsCache()
provider = KiteProvider(client=client, instruments=instruments)

class MyStrategy(Strategy):
    ...

pipeline = ResearchPipeline(provider=provider)
result = pipeline.run(
    symbols=["NSE:RELIANCE", "NSE:TCS"],
    start=dt.date(2024, 1, 1),
    end=dt.date(2024, 12, 31),
    frequency="day",
    strategy=MyStrategy(),
)
print(result.backtest_result.total_return)
```

`ResearchPipeline` applies the `nse_india_config` preset (0.12% blended
commission, 5 bps slippage) automatically. Override any field via
kwargs:

```python
pipeline = ResearchPipeline(
    provider=provider,
    initial_cash=10_000_000,  # Rs 1 crore
    commission_rate=0.0005,   # tighter than the default
)
```

## 3 &mdash; Zerodha charges (exact)

The preset's blended commission rate is an approximation. For exact
Zerodha charges &mdash; STT sell-side asymmetry, GST on brokerage,
per-segment stamp duty, SEBI fee &mdash; attach
`ZerodhaChargesModel` to your engine's fill post-processor:

```python
from ml4t.india.backtest import ZerodhaChargesModel, Segment

model = ZerodhaChargesModel(default_segment=Segment.EQUITY_DELIVERY)
# model.calculate(asset="NSE:RELIANCE", quantity=10, price=2500.0)
```

## 4 &mdash; Lot sizing for F&amp;O

```python
from ml4t.india.backtest import round_to_lot, floor_to_lot

# NIFTY options, lot_size=50
round_to_lot(76, 50)   # -> 100 (rounds up above half)
floor_to_lot(76, 50)   # -> 50  (floors for strict budget)
```

Both raise if the lot size is invalid or a non-zero quantity rounds
away to zero.

## 5 &mdash; Option chain + Greeks

```python
import datetime as dt
from ml4t.india.options import OptionChain, compute_greeks

chain = OptionChain.from_instruments(
    instruments.all(),
    underlying="NIFTY",
    expiry=dt.date(2026, 4, 24),
)

atm = chain.atm_strike(spot=25030.0)       # -> 25000
calls, puts = chain.around_atm(25030.0, count=2)

greeks = compute_greeks(
    flag="CE",
    spot=25030.0,
    strike=25000.0,
    time_to_expiry=21 / 365,
    risk_free_rate=0.07,
    volatility=0.15,
)
print(greeks.delta, greeks.theta)
```

`py_vollib` is used if installed (via `pip install ml4t-india[options]`);
otherwise a closed-form `math.erf` fallback runs &mdash; same BS math,
no extra dependency.

## 6 &mdash; Live trading

```python
from ml4t.india.live import KiteBroker, KiteTickerFeed, PostbackHandler
from ml4t.india.workflows import DeploymentPipeline

broker = KiteBroker(AsyncKiteClient(client))
feed = KiteTickerFeed(api_key="YOUR_API_KEY", access_token=record.access_token)
postbacks = PostbackHandler(api_secret="YOUR_API_SECRET")

pipeline = DeploymentPipeline(
    broker=broker,
    feed=feed,
    strategy=MyLiveStrategy(),     # object with on_tick / on_order
    instrument_tokens=[738561, 3465729],   # NSE:RELIANCE, NSE:TCS
    postbacks=postbacks,
    subscription_mode="quote",
)

import asyncio
asyncio.run(pipeline.start())
# ... strategy sees ticks + order postbacks ...
asyncio.run(pipeline.stop())
```

The strategy is duck-typed: only `on_tick(ticks)` and `on_order(order)`
are called if present. No base class to inherit from.

## 7 &mdash; Session awareness

```python
from ml4t.india.calendar import nse_calendar
import datetime as dt

cal = nse_calendar()
cal.is_session_day(dt.date(2026, 1, 26))  # False (Republic Day)
cal.next_session(dt.date(2026, 4, 24))    # 2026-04-27 (Friday -> Monday)
open_t, close_t = cal.session_bounds(dt.date(2026, 4, 22))
# open_t, close_t are IST-aware datetime objects
```

Use the calendar to gate live-trading startup or generate backtest
date ranges.

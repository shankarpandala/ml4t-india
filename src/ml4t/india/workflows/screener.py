"""Intraday strategy screener.

Fans out a cartesian (strategy x instrument) sweep of intraday backtests,
ranks each cell by a composite of risk-adjusted return and consistency,
and exposes equity curves + per-trade detail for the top performers.

The screener is built on three pieces:

* :class:`IntradayDataCache` -- Parquet-backed local cache of intraday
  OHLCV bars per ``(symbol, frequency)``. First call hits Kite via
  :class:`~ml4t.india.data.kite.KiteProvider` and chunks per Kite's
  per-interval ceiling; subsequent calls hit disk. Aging cache files
  are refreshed if the requested window extends beyond what's cached.

* :func:`run_cell` -- Self-contained ``(strategy, symbol)`` backtest.
  Builds the signals frame via :meth:`IntradayStrategy.compute_signals`,
  feeds it into :class:`ml4t.backtest.DataFeed`, and returns the raw
  :class:`ml4t.backtest.BacktestResult` plus a metrics summary.

* :class:`IntradayScreener` -- Orchestrator. Fans cells out via
  :class:`concurrent.futures.ThreadPoolExecutor`, computes the composite
  ranking score, and returns a :class:`ScreenerReport` with full grid
  + top-N details.

The screener does NOT trade. It only ranks. Live deployment is the
caller's responsibility -- see :class:`~ml4t.india.workflows.DeploymentPipeline`.
"""

from __future__ import annotations

import dataclasses
import datetime as dt
import logging
from collections.abc import Sequence
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np
import polars as pl
from ml4t.backtest import BacktestConfig, DataFeed, Engine
from ml4t.backtest.config import SlippageType
from ml4t.backtest.execution.impact import LinearImpact
from ml4t.backtest.types import AssetClass, ContractSpec
from ml4t.data.storage import HiveStorage, StorageConfig
from ml4t.diagnostic.evaluation.stats import deflated_sharpe_ratio

from ml4t.india.backtest.charges import Segment, ZerodhaChargesModel
from ml4t.india.core.futures import FuturesContract
from ml4t.india.data.kite import KiteProvider
from ml4t.india.kite.client import KiteClient
from ml4t.india.kite.instruments import InstrumentsCache
from ml4t.india.strategies import REGISTRY

log = logging.getLogger(__name__)

_IST = dt.timezone(dt.timedelta(hours=5, minutes=30), name="IST")

# Drop unreliable cells from the ranking. These thresholds are intentionally
# loose -- a strategy with 8 trades over 6 months is not statistical evidence
# of anything, even if the Sharpe looks great.
MIN_TRADES_FOR_RANKING = 20
MIN_PROFITABLE_MONTHS_FOR_RANKING = 3


def _contract_spec_for(
    symbol: str,
    asset_class: AssetClass = AssetClass.EQUITY,
    tick_size: float = 0.05,
) -> ContractSpec:
    """Build the upstream :class:`ContractSpec` for an Indian instrument.

    For cash equities and indices, ``asset_class=EQUITY`` and ``tick_size``
    is 0.05 on most NSE large caps. For F&O futures, ``asset_class=FUTURE``
    -- the screener still sizes by INR notional (multiplier=1) and treats
    lot rounding as a deploy-time concern, so backtest metrics are
    directly comparable across symbols regardless of lot size.
    """
    return ContractSpec(
        symbol=symbol,
        asset_class=asset_class,
        multiplier=1.0,
        tick_size=tick_size,
        currency="INR",
    )


# ----------------------------------------------------------------------
# Data cache
# ----------------------------------------------------------------------


class IntradayDataCache:
    """Per-symbol, per-frequency intraday OHLCV cache.

    Persistence is delegated to upstream :class:`ml4t.data.storage.HiveStorage`,
    which gives us atomic writes (tempfile + os.replace), file locking
    (so concurrent screener runs don't corrupt the cache), metadata
    tracking (provider + fetch timestamps), and Hive-style partitioning.
    The cache key is ``"<freq>/<symbol_underscored>"`` and the dataset is
    partitioned by month, so a 6-month minute cache lands as
    ``minute/NIFTY_50/year=2026/month=*/part-*.parquet``.
    """

    def __init__(self, cache_dir: Path | None = None) -> None:
        base = cache_dir or (Path.home() / ".ml4t" / "india" / "intraday_cache")
        base.mkdir(parents=True, exist_ok=True)
        self._dir = base
        self._storage = HiveStorage(
            StorageConfig(
                base_path=base,
                strategy="hive",
                compression="zstd",
                partition_granularity="month",
                atomic_writes=True,
                enable_locking=True,
                metadata_tracking=True,
                generate_profile=False,  # avoid expensive profile run on every write
            )
        )

    @staticmethod
    def _key(symbol: str, frequency: str) -> str:
        safe = symbol.replace(" ", "_").replace("/", "_")
        return f"{frequency}/{safe}"

    def get(
        self,
        symbol: str,
        start: dt.date,
        end: dt.date,
        frequency: str,
        provider: KiteProvider,
    ) -> pl.DataFrame:
        """Return bars in [start, end]; fetch + cache on miss or widening."""
        key = self._key(symbol, frequency)
        start_dt = dt.datetime.combine(start, dt.time.min)
        end_dt = dt.datetime.combine(end, dt.time.max)

        if self._storage.exists(key):
            cached = self._storage.read(key).collect()
            if cached.height:
                cached_start = cached["timestamp"].min().date()
                cached_end = cached["timestamp"].max().date()
                # Tolerate up to 2 days of trailing-edge slack: when the caller
                # asks for an end date that's a weekend / today-before-session,
                # cached data ending Friday / yesterday is still a valid hit.
                end_slack = end - dt.timedelta(days=2)
                if cached_start <= start and cached_end >= end_slack:
                    return cached.filter(
                        (pl.col("timestamp") >= start_dt)
                        & (pl.col("timestamp") <= end_dt)
                    ).sort("timestamp")
                log.info(
                    "cache widening %s: have %s..%s, need %s..%s",
                    key, cached_start, cached_end, start, end,
                )

        log.info("fetching %s %s..%s", key, start, end)
        df = provider.fetch_ohlcv(
            symbol=symbol,
            start=start.isoformat(),
            end=end.isoformat(),
            frequency=frequency,
        )
        # HiveStorage.write needs a partition col it can derive year/month
        # from. timestamp works -- StorageConfig's partition_granularity
        # tells the backend to bucket by year/month.
        self._storage.write(
            df,
            key=key,
            metadata={
                "symbol": symbol,
                "frequency": frequency,
                "fetched_at": dt.datetime.now(_IST).isoformat(),
                "row_count": df.height,
            },
        )
        return df


# ----------------------------------------------------------------------
# Cell metrics
# ----------------------------------------------------------------------


@dataclass
class CellMetrics:
    """Metrics for one (strategy, symbol) backtest cell.

    Combines upstream ``BacktestResult.metrics`` with India-specific
    extensions (monthly consistency, charge-adjusted P&L).
    """

    strategy: str
    symbol: str
    num_trades: int
    win_rate: float
    total_return_pct: float
    sharpe: float
    sortino: float
    calmar: float
    max_drawdown_pct: float
    profit_factor: float
    monthly_consistency: float  # fraction of months with positive P&L
    profitable_months: int
    total_months: int
    longest_losing_streak_trades: int
    composite_score: float
    rank_eligible: bool
    # Charge breakdown (Zerodha intraday, post-hoc on fills)
    gross_pnl: float
    zerodha_charges: float
    net_pnl: float
    # Statistical-significance (ml4t.diagnostic).
    # psr_probability is the per-cell Probabilistic Sharpe Ratio --
    # P(true Sharpe > 0 | observed returns). dsr_probability is the
    # multi-cell Deflated Sharpe Ratio -- same probability adjusted for
    # the fact that we tested K cells, so the best one's Sharpe is
    # inflated by selection bias. dsr is filled in after the fan-out;
    # psr per-cell.
    psr_probability: float = 0.0
    dsr_probability: float | None = None


@dataclass
class CellResult:
    """Full backtest output for one cell: metrics + equity curve + trades."""

    metrics: CellMetrics
    equity_curve: pl.DataFrame  # cols: timestamp, equity
    trades: pl.DataFrame  # whatever to_trades_dataframe produces
    monthly_pnl: pl.DataFrame  # cols: month (date), pnl
    daily_returns: np.ndarray  # daily % returns for DSR / tear sheet


@dataclass
class ScreenerReport:
    """Whole-screener output: grid + top-N drilldown."""

    grid: pl.DataFrame  # one row per cell, sorted by composite_score desc
    cells: dict[tuple[str, str], CellResult] = field(default_factory=dict)
    top_n: int = 3

    def winners(self, n: int | None = None) -> list[CellResult]:
        """Return the top-N cells for drill-down rendering.

        Preference order: eligible cells (>= 20 trades AND >= 3 profitable
        months) come first, ranked by composite score; if fewer than N
        eligible cells exist we fall through to the highest-Sharpe
        ineligible cells so the report always has something to plot.
        Each :class:`CellMetrics` carries ``rank_eligible`` so the markdown
        renderer can flag the difference.
        """
        n = n if n is not None else self.top_n
        eligible = self.grid.filter(pl.col("rank_eligible"))
        ineligible = self.grid.filter(~pl.col("rank_eligible"))
        ordered = pl.concat([eligible, ineligible]) if eligible.height else ineligible
        out: list[CellResult] = []
        for row in ordered.head(n).iter_rows(named=True):
            key = (row["strategy"], row["symbol"])
            if key in self.cells:
                out.append(self.cells[key])
        return out


# ----------------------------------------------------------------------
# Single cell backtest
# ----------------------------------------------------------------------


def _compute_monthly_pnl(equity: pl.DataFrame) -> pl.DataFrame:
    """Reduce the equity curve to month-bucketed P&L (end - start per month)."""
    if equity.height == 0:
        return pl.DataFrame(schema={"month": pl.Date, "pnl": pl.Float64})
    e = equity.sort("timestamp").with_columns(
        pl.col("timestamp").dt.truncate("1mo").dt.date().alias("month")
    )
    agg = e.group_by("month").agg(
        pl.col("equity").first().alias("_open"),
        pl.col("equity").last().alias("_close"),
    ).sort("month")
    return agg.with_columns((pl.col("_close") - pl.col("_open")).alias("pnl")).select(
        ["month", "pnl"]
    )


def _daily_returns(equity: pl.DataFrame) -> np.ndarray:
    """Collapse an intraday equity curve to daily % returns (close-to-close)."""
    if equity.height == 0:
        return np.array([], dtype=float)
    daily = (
        equity.sort("timestamp")
        .with_columns(pl.col("timestamp").dt.date().alias("_day"))
        .group_by("_day")
        .agg(pl.col("equity").last())
        .sort("_day")
    )
    if daily.height < 2:
        return np.array([], dtype=float)
    rets = daily["equity"].pct_change().drop_nulls().to_numpy()
    # Strip non-finite (degenerate sessions with zero equity).
    return rets[np.isfinite(rets)]


def _compute_longest_losing_streak(trades: pl.DataFrame) -> int:
    """Longest consecutive run of losing trades (pnl <= 0)."""
    if trades.height == 0 or "pnl" not in trades.columns:
        return 0
    longest = current = 0
    for pnl in trades["pnl"].to_list():
        if pnl is not None and pnl <= 0:
            current += 1
            longest = max(longest, current)
        else:
            current = 0
    return longest


def _safe_metric(metrics: dict[str, Any], key: str, default: float = 0.0) -> float:
    v = metrics.get(key, default)
    try:
        f = float(v)
    except (TypeError, ValueError):
        return default
    if f != f or f in (float("inf"), float("-inf")):  # NaN or inf
        return default
    return f


def run_cell(
    strategy_name: str,
    symbol: str,
    prices: pl.DataFrame,
    position_value: float,
    initial_cash: float,
    strategy_params: dict[str, Any] | None = None,
    charges_segment: Segment = Segment.EQUITY_INTRADAY,
    asset_class: AssetClass = AssetClass.EQUITY,
    tick_size: float = 0.05,
    label: str | None = None,
) -> CellResult:
    """Run one (strategy, symbol) backtest and return metrics + curves.

    ``label`` defaults to ``symbol`` but lets callers tag a cell with a
    different name in reports -- used when fetching cash OHLCV under a
    futures cell label (e.g. fetch 'RELIANCE' but report
    'RELIANCE26MAYFUT' under EQUITY_FUTURES charges).
    """
    cell_label = label or symbol
    if strategy_name not in REGISTRY:
        raise ValueError(f"unknown strategy {strategy_name!r}; known: {list(REGISTRY)}")
    cls = REGISTRY[strategy_name]
    params = strategy_params or {}

    # Build a canonical single-symbol frame for DataFeed.
    df = prices.sort("timestamp")
    if "symbol" not in df.columns:
        df = df.with_columns(pl.lit(symbol).alias("symbol"))
    else:
        df = df.with_columns(pl.lit(symbol).alias("symbol"))

    # Precompute strategy signals.
    signals = cls.compute_signals(df, **params)

    feed = DataFeed(prices_df=df, signals_df=signals)
    config = BacktestConfig(
        initial_cash=initial_cash,
        # Engine commission disabled: we apply the realistic Zerodha
        # charge stack (brokerage + STT/CTT + GST + exchange + SEBI + stamp)
        # post-hoc on the fills. Leaving the engine commission also active
        # would double-count brokerage and inflate slippage on the equity
        # curve.
        commission_rate=0.0,
        # Static slippage is intentionally tiny: a small fixed floor applied
        # in addition to the size-scaled LinearImpact(coefficient=0.05) model
        # below. Set slippage_type explicitly. In b21 Broker.from_config
        # infers PERCENTAGE from a positive slippage_rate, but we state it
        # explicitly so behavior is unambiguous and robust if that inference
        # shim is ever removed.
        slippage_type=SlippageType.PERCENTAGE,
        slippage_rate=0.00005,
        allow_short_selling=True,
    )

    strategy = cls(position_value=position_value, **params)
    # ContractSpec gives the engine per-symbol tick + currency awareness;
    # LinearImpact charges a size-scaled extra slippage so large orders
    # hurt more than tiny ones -- closer to real intraday execution than
    # a flat slippage_rate.
    result = Engine(
        feed=feed,
        strategy=strategy,
        config=config,
        contract_specs={symbol: _contract_spec_for(
            symbol, asset_class=asset_class, tick_size=tick_size,
        )},
        market_impact_model=LinearImpact(coefficient=0.05),
    ).run()

    # Reshape outputs into Polars frames for downstream consumption.
    eq_pairs = result.equity_curve
    equity_df = pl.DataFrame(
        {"timestamp": [t for t, _ in eq_pairs], "equity": [e for _, e in eq_pairs]}
    )
    trades_df = (
        result.to_trades_dataframe()
        if hasattr(result, "to_trades_dataframe")
        else pl.DataFrame()
    )

    monthly_pnl = _compute_monthly_pnl(equity_df)
    daily_rets = _daily_returns(equity_df)

    # Per-cell PSR (Probabilistic Sharpe Ratio): P(true Sharpe > 0).
    # Needs at least 2 observations.
    if daily_rets.size >= 2:
        try:
            psr_res = deflated_sharpe_ratio(
                daily_rets, frequency="daily", benchmark_sharpe=0.0
            )
            psr_prob = float(psr_res.to_dict().get("probability", 0.0))
        except Exception:  # noqa: BLE001
            psr_prob = 0.0
    else:
        psr_prob = 0.0

    # Apply ZerodhaChargesModel to every fill -- this is the realistic
    # cost model; the BacktestConfig commission is just a stand-in to keep
    # the engine honest. The model takes asset, signed quantity, price.
    # Pass charges_segment so futures cells use Segment.EQUITY_FUTURES
    # (flat Rs 20 + lower stat charges) rather than the equity intraday stack.
    fills = getattr(result, "fills", []) or []
    charges_model = ZerodhaChargesModel(default_segment=charges_segment)
    total_charges = 0.0
    for f in fills:
        qty = float(getattr(f, "quantity", 0) or 0)
        # Fills carry the unsigned quantity; sign reflects the side.
        side = getattr(f, "side", None)
        if side is not None and str(side).upper().endswith("SELL"):
            qty = -abs(qty)
        else:
            qty = abs(qty)
        price = float(getattr(f, "price", 0) or 0)
        asset = str(getattr(f, "asset", symbol))
        total_charges += charges_model.calculate(asset=asset, quantity=qty, price=price)

    metrics_d = dict(result.metrics)
    # Engine commission is now zero (see config above) -- so total_return
    # IS the gross P&L fraction before realistic broker charges. Convert
    # to INR via (final_value - initial_cash); net_pnl subtracts the
    # post-hoc Zerodha stack so callers see the deployable bottom line.
    final_value = _safe_metric(metrics_d, "final_value", initial_cash)
    gross_pnl = final_value - initial_cash
    net_pnl = gross_pnl - total_charges

    consistency = (
        float((monthly_pnl["pnl"] > 0).sum() / monthly_pnl.height)
        if monthly_pnl.height
        else 0.0
    )
    profitable_months = int((monthly_pnl["pnl"] > 0).sum()) if monthly_pnl.height else 0

    sharpe = _safe_metric(metrics_d, "sharpe", 0.0)
    calmar = _safe_metric(metrics_d, "calmar", 0.0)
    num_trades = int(_safe_metric(metrics_d, "num_trades", 0))
    losing_streak = _compute_longest_losing_streak(trades_df)

    rank_eligible = (
        num_trades >= MIN_TRADES_FOR_RANKING
        and profitable_months >= MIN_PROFITABLE_MONTHS_FOR_RANKING
    )

    # Composite score: reward strategies that ride high Sharpe, recover
    # quickly from drawdowns (Calmar), and produce P&L every month.
    # Negative-Sharpe cells get a zero score so they sink to the bottom.
    composite = max(0.0, sharpe) * max(0.0, calmar) * consistency

    cell_metrics = CellMetrics(
        strategy=strategy_name,
        symbol=cell_label,
        num_trades=num_trades,
        win_rate=_safe_metric(metrics_d, "win_rate", 0.0),
        total_return_pct=_safe_metric(metrics_d, "total_return_pct", 0.0),
        sharpe=sharpe,
        sortino=_safe_metric(metrics_d, "sortino", 0.0),
        calmar=calmar,
        max_drawdown_pct=_safe_metric(metrics_d, "max_drawdown_pct", 0.0),
        profit_factor=_safe_metric(metrics_d, "profit_factor", 0.0),
        monthly_consistency=consistency,
        profitable_months=profitable_months,
        total_months=monthly_pnl.height,
        longest_losing_streak_trades=losing_streak,
        composite_score=composite,
        rank_eligible=rank_eligible,
        gross_pnl=gross_pnl,
        zerodha_charges=total_charges,
        net_pnl=net_pnl,
        psr_probability=psr_prob,
        dsr_probability=None,  # filled in after fan-out
    )
    return CellResult(
        metrics=cell_metrics,
        equity_curve=equity_df,
        trades=trades_df,
        monthly_pnl=monthly_pnl,
        daily_returns=daily_rets,
    )


# ----------------------------------------------------------------------
# Screener
# ----------------------------------------------------------------------


class IntradayScreener:
    """Cartesian (strategy x instrument) intraday backtest sweep."""

    def __init__(
        self,
        client: KiteClient,
        instruments: InstrumentsCache,
        cache: IntradayDataCache | None = None,
        max_workers: int = 6,
    ) -> None:
        self._client = client
        self._instruments = instruments
        self._cache = cache or IntradayDataCache()
        self._provider = KiteProvider(client=client, instruments=instruments)
        self._max_workers = int(max_workers)

    def run(
        self,
        strategies: Sequence[str],
        symbols: Sequence[str] | Sequence[FuturesContract],
        start: dt.date,
        end: dt.date,
        frequency: str = "minute",
        position_value: float = 100_000.0,
        initial_cash: float = 1_000_000.0,
        strategy_params: dict[str, dict[str, Any]] | None = None,
        top_n: int = 3,
    ) -> ScreenerReport:
        """Fan out N strategies x M symbols backtests; rank by composite score.

        ``symbols`` may be either a plain list of cash tradingsymbols OR a
        list of :class:`FuturesContract` objects. In the futures case we
        fetch OHLCV under the underlying cash symbol (we don't have
        multi-month historical data for monthly futures contracts), label
        the cell with the futures tradingsymbol, swap the charges segment
        to ``EQUITY_FUTURES``, and tag the contract spec as
        ``AssetClass.FUTURE``. Intraday cash-vs-futures basis is small
        enough that this is the standard practitioner approach for F&O
        strategy ranking.
        """
        strategy_params = strategy_params or {}

        # Normalise to (fetch_symbol, label, asset_class, tick_size, charges) tuples.
        cells_spec: list[tuple[str, str, AssetClass, float, Segment]] = []
        for s in symbols:
            if isinstance(s, FuturesContract):
                cells_spec.append((
                    s.underlying,           # fetch via cash underlying
                    s.tradingsymbol,        # label with futures symbol
                    AssetClass.FUTURE,
                    s.tick_size,
                    Segment.EQUITY_FUTURES,
                ))
            else:
                cells_spec.append((
                    s, s, AssetClass.EQUITY, 0.05, Segment.EQUITY_INTRADAY,
                ))

        # 1) Fetch + cache data per UNDERLYING (deduplicated -- multiple
        #    futures cells may share the same underlying cash data).
        unique_fetch = list({fetch for fetch, *_ in cells_spec})
        symbol_data: dict[str, pl.DataFrame] = {}
        skipped: list[tuple[str, str]] = []
        for fetch in unique_fetch:
            try:
                df = self._cache.get(
                    symbol=fetch,
                    start=start,
                    end=end,
                    frequency=frequency,
                    provider=self._provider,
                )
            except Exception as exc:  # noqa: BLE001
                log.warning("skipping %s: %s", fetch, exc)
                skipped.append((fetch, f"{type(exc).__name__}: {exc}"))
                continue
            if df.is_empty():
                log.warning("skipping %s: provider returned empty frame", fetch)
                skipped.append((fetch, "empty data"))
                continue
            symbol_data[fetch] = df

        if skipped:
            log.warning(
                "%d/%d underlyings skipped due to errors / no data",
                len(skipped), len(unique_fetch),
            )

        # 2) Backtest each cell in parallel. Only cells whose underlying
        #    fetched successfully participate.
        cells: dict[tuple[str, str], CellResult] = {}
        with ThreadPoolExecutor(max_workers=self._max_workers) as pool:
            future_to_key = {}
            for strat in strategies:
                for fetch, label, asset_cls, tick, segment in cells_spec:
                    if fetch not in symbol_data:
                        continue
                    fut = pool.submit(
                        run_cell,
                        strat,
                        fetch,
                        symbol_data[fetch],
                        position_value,
                        initial_cash,
                        strategy_params.get(strat),
                        segment,
                        asset_cls,
                        tick,
                        label,
                    )
                    future_to_key[fut] = (strat, label)
            for fut in as_completed(future_to_key):
                key = future_to_key[fut]
                try:
                    cells[key] = fut.result()
                except Exception as exc:  # noqa: BLE001
                    log.exception("cell %s failed: %s", key, exc)

        # 3) Run the multi-cell Deflated Sharpe Ratio. We tested K cells, so
        #    the best one's Sharpe is inflated by selection bias. DSR
        #    accounts for that by deflating against the expected max Sharpe
        #    of K i.i.d. zero-Sharpe trials. We compute it once over every
        #    cell's daily-return series and write the resulting per-cell
        #    probability back onto each CellMetrics.
        viable = [
            (key, cell)
            for key, cell in cells.items()
            if cell.daily_returns.size >= 5
        ]
        if len(viable) >= 2:
            try:
                series = [cell.daily_returns for _, cell in viable]
                dsr_res = deflated_sharpe_ratio(
                    series, frequency="daily", benchmark_sharpe=0.0,
                )
                # dsr_res reports the best-of-K cell's deflated probability.
                # Assign it to the cell with the highest raw Sharpe in the
                # viable subset -- the one DSR was effectively computed on.
                best_key = max(
                    viable, key=lambda kv: kv[1].metrics.sharpe,
                )[0]
                cells[best_key].metrics.dsr_probability = float(
                    dsr_res.to_dict().get("probability", 0.0)
                )
            except Exception as exc:  # noqa: BLE001
                log.warning("DSR computation failed: %s", exc)

        # 4) Build the grid frame.
        # infer_schema_length=None scans every row -- needed because some
        # statistical metrics (DSR, etc.) produce sub-normal float values
        # (~1e-200) for rare cells that polars can't infer from the first
        # 100 rows; without this, construction raises ComputeError.
        grid_rows = [dataclasses.asdict(c.metrics) for c in cells.values()]
        if grid_rows:
            grid = pl.DataFrame(grid_rows, infer_schema_length=None).sort(
                ["composite_score", "sharpe"], descending=[True, True]
            )
        else:
            grid = pl.DataFrame()

        return ScreenerReport(grid=grid, cells=cells, top_n=top_n)


__all__ = [
    "CellMetrics",
    "CellResult",
    "IntradayDataCache",
    "IntradayScreener",
    "ScreenerReport",
    "run_cell",
]

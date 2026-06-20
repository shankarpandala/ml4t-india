"""Intraday strategy screener -- CLI entrypoint.

Runs every strategy in :data:`ml4t.india.strategies.REGISTRY` against
every symbol in the chosen universe over ``--months`` of intraday data,
ranks the (strategy, symbol) cells by composite score, and writes a
report bundle (markdown summary + CSV grid + per-winner PNGs).

Usage
-----

::

    # Default: NIFTY 50 / NIFTY BANK / NIFTY FIN SERVICE @ 1-min, 6 months
    python scripts/screen_intraday.py

    # Smaller smoke run
    python scripts/screen_intraday.py --months 1

    # Different bar frequency
    python scripts/screen_intraday.py --frequency 5minute --months 6

Requires a current Kite token at ``~/.ml4t/india/token.json`` (run
``ml4t-india login`` once per trading day).
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import logging
import sys
from pathlib import Path

import matplotlib.pyplot as plt
import polars as pl

try:
    from ml4t.diagnostic.visualization.backtest import generate_backtest_tearsheet
except ImportError as exc:  # pragma: no cover - optional viz extra
    raise SystemExit(
        "generate_backtest_tearsheet requires the plotly-based viz extra. "
        "Install with: pip install 'ml4t-india[viz]'"
    ) from exc

from ml4t.india.core.futures import resolve_futures
from ml4t.india.core.universe import PRESETS
from ml4t.india.kite.auth import default_token_path, load_token
from ml4t.india.kite.client import KiteClient
from ml4t.india.kite.instruments import InstrumentsCache
from ml4t.india.strategies import REGISTRY
from ml4t.india.workflows.screener import (
    CellResult,
    IntradayScreener,
    ScreenerReport,
)

# Map preset symbols to NFO underlying names: the indices in cash use
# "NIFTY 50" / "NIFTY BANK" / "NIFTY FIN SERVICE", but their futures
# trade under shorter aliases ("NIFTY", "BANKNIFTY", "FINNIFTY").
INDEX_CASH_TO_FUT_UNDERLYING: dict[str, str] = {
    "NIFTY 50": "NIFTY",
    "NIFTY BANK": "BANKNIFTY",
    "NIFTY FIN SERVICE": "FINNIFTY",
}

DEFAULT_UNIVERSE: list[str] = list(PRESETS["indices"])


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Intraday strategy screener (Indian indices, Zerodha Kite data).",
    )
    p.add_argument("--months", type=int, default=6, help="History window in months.")
    p.add_argument(
        "--frequency",
        type=str,
        default="minute",
        help="Kite bar frequency: minute, 3minute, 5minute, 15minute, day.",
    )
    p.add_argument(
        "--universe",
        nargs="*",
        default=None,
        help="Explicit tradingsymbols list (overrides --universe-preset).",
    )
    p.add_argument(
        "--universe-preset",
        choices=sorted(PRESETS),
        default="indices",
        help=(
            "Pre-baked universe: 'indices' (3 indices), 'nifty50' (50 stocks), "
            "'banknifty' (12 bank stocks), 'finnifty' (20 financial-sector stocks), "
            "'nifty_fnf' (deduplicated union, ~65 symbols)."
        ),
    )
    p.add_argument(
        "--asset-class",
        choices=("cash", "futures"),
        default="cash",
        help=(
            "'cash' (default): backtest on cash equity / index OHLCV with "
            "EQUITY_INTRADAY (MIS) Zerodha charges. 'futures': re-label "
            "cells as the front-month F&O contracts, swap to EQUITY_FUTURES "
            "Zerodha charges (~Rs 40 flat per round trip), tag asset_class "
            "as FUTURE. Backtest still uses cash OHLCV because monthly "
            "futures contracts only have ~30 days of history -- intraday "
            "basis is small enough that this is the standard approach."
        ),
    )
    p.add_argument(
        "--strategies",
        nargs="*",
        default=list(REGISTRY),
        help="Strategy names (default: all in REGISTRY).",
    )
    p.add_argument("--top-n", type=int, default=3, help="How many winner cells to drill down.")
    p.add_argument(
        "--initial-cash",
        type=float,
        default=1_000_000.0,
        help="Starting capital in INR (default 10L).",
    )
    p.add_argument(
        "--position-value",
        type=float,
        default=100_000.0,
        help="Notional INR per trade (default 1L).",
    )
    p.add_argument("--workers", type=int, default=6, help="ThreadPool workers.")
    p.add_argument(
        "--out-dir",
        type=Path,
        default=Path("reports/screener"),
        help="Where to write report files.",
    )
    p.add_argument("--verbose", "-v", action="store_true")
    return p.parse_args()


def _build_client() -> KiteClient:
    record = load_token()
    if record is None:
        raise SystemExit(
            f"No cached Kite token at {default_token_path()}. "
            "Run `ml4t-india login` first."
        )
    if record.is_expired():
        raise SystemExit(
            "Cached Kite token has expired (rotates at ~06:00 IST). "
            "Run `ml4t-india login` again."
        )
    return KiteClient.from_api_key(api_key=record.api_key, access_token=record.access_token)


def _ensure_instruments(client: KiteClient) -> InstrumentsCache:
    cache = InstrumentsCache()
    if cache.is_stale():
        cache.refresh(client)
    return cache


def _format_grid_markdown(grid: pl.DataFrame, top_k: int = 10) -> str:
    """Render the top-K rows of the grid as a markdown table."""
    if grid.is_empty():
        return "_no cells_"
    cols = [
        "strategy",
        "symbol",
        "composite_score",
        "sharpe",
        "psr_probability",
        "dsr_probability",
        "calmar",
        "monthly_consistency",
        "total_return_pct",
        "max_drawdown_pct",
        "win_rate",
        "num_trades",
        "profitable_months",
        "rank_eligible",
    ]
    head = grid.head(top_k).select([c for c in cols if c in grid.columns])
    lines = ["| " + " | ".join(head.columns) + " |"]
    lines.append("| " + " | ".join("---" for _ in head.columns) + " |")
    for row in head.iter_rows(named=True):
        cells = []
        for c in head.columns:
            v = row[c]
            if isinstance(v, float):
                cells.append(f"{v:.4f}" if abs(v) < 100 else f"{v:.2f}")
            elif isinstance(v, bool):
                cells.append("yes" if v else "no")
            else:
                cells.append(str(v))
        lines.append("| " + " | ".join(cells) + " |")
    return "\n".join(lines)


def _plot_equity_curve(cell: CellResult, out_path: Path) -> None:
    if cell.equity_curve.is_empty():
        return
    ts = cell.equity_curve["timestamp"].to_list()
    eq = cell.equity_curve["equity"].to_list()
    fig, ax = plt.subplots(figsize=(10, 4))
    ax.plot(ts, eq, linewidth=0.8)
    ax.set_title(f"{cell.metrics.strategy} on {cell.metrics.symbol} -- equity curve")
    ax.set_xlabel("Time")
    ax.set_ylabel("Portfolio equity (INR)")
    ax.grid(True, alpha=0.3)
    fig.autofmt_xdate()
    fig.tight_layout()
    fig.savefig(out_path, dpi=120)
    plt.close(fig)


def _write_tearsheet(cell: CellResult, out_path: Path) -> bool:
    """Generate the upstream ml4t.diagnostic full backtest tear sheet (HTML).

    Returns True if the file was written; False if the cell had no
    equity curve or the upstream renderer raised.
    """
    if cell.equity_curve.is_empty():
        return False
    try:
        m = cell.metrics
        # The upstream renderer accepts both equity_curve + returns; we feed
        # both because the dashboard derives different panels from each.
        html = generate_backtest_tearsheet(
            equity_curve=cell.equity_curve,
            returns=pl.Series("returns", cell.daily_returns),
            trades=cell.trades if not cell.trades.is_empty() else None,
            metrics={
                "sharpe": m.sharpe,
                "sortino": m.sortino,
                "calmar": m.calmar,
                "total_return": m.total_return_pct / 100.0,
                "max_drawdown": m.max_drawdown_pct / 100.0,
                "num_trades": m.num_trades,
                "win_rate": m.win_rate,
                "profit_factor": m.profit_factor,
            },
            title=f"{m.strategy} on {m.symbol}",
            subtitle=f"PSR={m.psr_probability:.1%}  "
                     f"DSR={m.dsr_probability:.1%}" if m.dsr_probability is not None
                     else f"PSR={m.psr_probability:.1%}",
            template="full",
            theme="default",
            output_path=str(out_path),
        )
        return bool(html)
    except Exception as exc:  # noqa: BLE001
        print(f"  tear sheet skipped ({type(exc).__name__}: {exc})")
        return False


def _plot_monthly_pnl(cell: CellResult, out_path: Path) -> None:
    if cell.monthly_pnl.is_empty():
        return
    months = [m.strftime("%Y-%m") for m in cell.monthly_pnl["month"].to_list()]
    pnls = cell.monthly_pnl["pnl"].to_list()
    colors = ["#2ca02c" if p > 0 else "#d62728" for p in pnls]
    fig, ax = plt.subplots(figsize=(10, 4))
    ax.bar(months, pnls, color=colors)
    ax.axhline(0, color="black", linewidth=0.5)
    ax.set_title(f"{cell.metrics.strategy} on {cell.metrics.symbol} -- monthly P&L")
    ax.set_xlabel("Month")
    ax.set_ylabel("Net P&L (INR)")
    ax.grid(True, axis="y", alpha=0.3)
    fig.tight_layout()
    fig.savefig(out_path, dpi=120)
    plt.close(fig)


def _write_report(
    report: ScreenerReport,
    args: argparse.Namespace,
    out_dir: Path,
) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = dt.datetime.now().strftime("%Y%m%d_%H%M%S")
    grid_csv = out_dir / f"grid_{stamp}.csv"
    summary_md = out_dir / f"summary_{stamp}.md"
    daily_returns_parquet = out_dir / f"daily_returns_{stamp}.parquet"
    if not report.grid.is_empty():
        report.grid.write_csv(grid_csv)

    # Persist per-cell daily returns so downstream meta-models
    # (scripts/portfolio_allocator.py) can build features on the cell-level
    # P&L without re-running the backtests. One row per (strategy, symbol,
    # day) tuple.
    drow_chunks: list[pl.DataFrame] = []
    for (strat, sym), cell in report.cells.items():
        if cell.daily_returns.size == 0 or cell.equity_curve.is_empty():
            continue
        # Pair each return with the closing date of its session.
        eq = cell.equity_curve.sort("timestamp").with_columns(
            pl.col("timestamp").dt.date().alias("_day"),
        )
        daily = eq.group_by("_day").agg(pl.col("equity").last()).sort("_day")
        # daily_returns has one fewer entry than daily (pct_change drops first).
        dates = daily["_day"].to_list()[1:]
        rets = cell.daily_returns.tolist()
        if len(dates) != len(rets):
            n = min(len(dates), len(rets))
            dates, rets = dates[-n:], rets[-n:]
        drow_chunks.append(pl.DataFrame({
            "date": dates,
            "strategy": [strat] * len(dates),
            "symbol": [sym] * len(dates),
            "daily_return": rets,
        }))
    if drow_chunks:
        pl.concat(drow_chunks, how="vertical").write_parquet(daily_returns_parquet)

    md_lines: list[str] = [
        f"# Intraday screener report -- {stamp}",
        "",
        "## Run config",
        "",
        "```",
        json.dumps(
            {
                "months": args.months,
                "frequency": args.frequency,
                "universe_preset": args.universe_preset,
                "universe_size": len(args.universe) if args.universe else len(PRESETS[args.universe_preset]),
                "asset_class": args.asset_class,
                "strategies": args.strategies,
                "initial_cash": args.initial_cash,
                "position_value": args.position_value,
            },
            indent=2,
        ),
        "```",
        "",
        "## Top 10 cells by composite score",
        "",
        _format_grid_markdown(report.grid, top_k=10),
        "",
        f"_Full grid: `{grid_csv.name}` ({report.grid.height} cells)_",
        "",
        "## Winners (drill-down)",
        "",
    ]

    winners = report.winners()
    if not winners:
        md_lines += [
            "_No cells produced output (data fetch failed?)._",
        ]
    for i, cell in enumerate(winners, start=1):
        m = cell.metrics
        eligible_tag = " (eligible)" if m.rank_eligible else " (best-available, NOT eligible by threshold)"
        base = f"winner_{i}_{m.strategy}_{m.symbol.replace(' ', '_')}"
        eq_png = out_dir / f"{base}_equity.png"
        pnl_png = out_dir / f"{base}_monthly.png"
        tear_html = out_dir / f"{base}_tearsheet.html"
        trades_csv = out_dir / f"{base}_trades.csv"
        _plot_equity_curve(cell, eq_png)
        _plot_monthly_pnl(cell, pnl_png)
        tear_ok = _write_tearsheet(cell, tear_html)
        if not cell.trades.is_empty():
            cell.trades.write_csv(trades_csv)

        # Probabilistic Sharpe (per-cell) + Deflated Sharpe (multi-cell).
        psr_line = f"- PSR (probability of true Sharpe > 0): **{m.psr_probability:.1%}**"
        if m.dsr_probability is not None:
            psr_line += (
                f"  /  DSR (deflated for multiple-testing across cells): "
                f"**{m.dsr_probability:.1%}**"
            )

        md_lines += [
            f"### #{i}: {m.strategy} on {m.symbol}{eligible_tag}",
            "",
            f"- Composite score: **{m.composite_score:.4f}**",
            f"- Sharpe: {m.sharpe:.3f}  /  Sortino: {m.sortino:.3f}  /  Calmar: {m.calmar:.3f}",
            f"- Total return: {m.total_return_pct:.2f}%  /  Max DD: {m.max_drawdown_pct:.2f}%",
            f"- Win rate: {m.win_rate:.2%}  /  Profit factor: {m.profit_factor:.2f}",
            f"- Monthly consistency: {m.monthly_consistency:.0%} "
            f"({m.profitable_months}/{m.total_months} months profitable)",
            f"- Trades: {m.num_trades}  /  Longest losing streak: {m.longest_losing_streak_trades}",
            f"- Gross P&L: Rs {m.gross_pnl:,.0f}  /  Zerodha charges (intraday est.): "
            f"Rs {m.zerodha_charges:,.0f}  /  Net P&L: Rs {m.net_pnl:,.0f}",
            psr_line,
            "",
            f"![equity]({eq_png.name})",
            "",
            f"![monthly]({pnl_png.name})",
            "",
            f"_Full interactive tear sheet (ml4t.diagnostic): `{tear_html.name}`_"
            if tear_ok
            else "_(tear sheet skipped)_",
            "",
            f"_Trades: `{trades_csv.name}`_" if not cell.trades.is_empty() else "_(no trades)_",
            "",
        ]

    summary_md.write_text("\n".join(md_lines), encoding="utf-8")
    return summary_md


def _print_console_summary(report: ScreenerReport) -> None:
    grid = report.grid
    if grid.is_empty():
        print("\n(no cells produced -- check data + strategies)")
        return
    print("\n=== Top 10 cells by composite score ===")
    print(_format_grid_markdown(grid, top_k=10))
    eligible = grid.filter(pl.col("rank_eligible"))
    print(
        f"\nEligible cells: {eligible.height}/{grid.height} "
        f"(filtered by >= 20 trades AND >= 3 profitable months)"
    )


def main() -> int:
    args = _parse_args()
    logging.basicConfig(
        level=logging.INFO if args.verbose else logging.WARNING,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    end = dt.date.today()
    # Approximate months -> 30 days. Sessions filtered downstream.
    start = end - dt.timedelta(days=args.months * 30)

    # Resolve universe: explicit --universe wins, else load the preset.
    cash_universe = args.universe if args.universe else list(PRESETS[args.universe_preset])

    print(f"\nWindow: {start} .. {end} ({args.months} months) @ {args.frequency}")
    src = "explicit" if args.universe else f"preset={args.universe_preset!r}"
    print(f"Cash universe ({len(cash_universe)}, {src}): {cash_universe}")
    print(f"Asset class: {args.asset_class}")
    print(f"Strategies ({len(args.strategies)}): {args.strategies}")

    client = _build_client()
    instruments = _ensure_instruments(client)
    screener = IntradayScreener(
        client=client, instruments=instruments, max_workers=args.workers
    )

    # When the user asked for futures, map cash tradingsymbols to front-month
    # futures contracts. Indices are aliased ("NIFTY 50" -> "NIFTY", etc.)
    # so the NFO lookup finds the right futures.
    if args.asset_class == "futures":
        underlyings = [
            INDEX_CASH_TO_FUT_UNDERLYING.get(s, s) for s in cash_universe
        ]
        futures = resolve_futures(instruments, underlyings)
        if not futures:
            raise SystemExit("no F&O futures resolved from this universe.")
        print(f"Resolved {len(futures)}/{len(underlyings)} front-month futures")
        # Remap each FuturesContract's `underlying` back to the cash symbol
        # the IntradayDataCache already has (e.g. 'NIFTY' -> 'NIFTY 50').
        fut_to_cash = {v: k for k, v in INDEX_CASH_TO_FUT_UNDERLYING.items()}
        remapped = []
        for f in futures:
            cash_sym = fut_to_cash.get(f.underlying, f.underlying)
            remapped.append(type(f)(
                underlying=cash_sym,
                tradingsymbol=f.tradingsymbol,
                expiry=f.expiry,
                lot_size=f.lot_size,
                tick_size=f.tick_size,
                instrument_token=f.instrument_token,
            ))
        symbols_for_run: list = remapped
        print(f"Total cells: {len(remapped) * len(args.strategies)}\n")
    else:
        symbols_for_run = cash_universe
        print(f"Total cells: {len(cash_universe) * len(args.strategies)}\n")

    report = screener.run(
        strategies=args.strategies,
        symbols=symbols_for_run,
        start=start,
        end=end,
        frequency=args.frequency,
        position_value=args.position_value,
        initial_cash=args.initial_cash,
        top_n=args.top_n,
    )

    _print_console_summary(report)
    summary_path = _write_report(report, args, args.out_dir)
    print(f"\nReport written: {summary_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

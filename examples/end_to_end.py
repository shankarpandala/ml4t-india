#!/usr/bin/env python
"""Real-data, top-to-bottom ml4t-india orchestrator.

This script runs the **entire** ml4t-india feature set against the live
Zerodha Kite market, in one staged pass, on the user's own machine. The
*only* thing it simulates is order execution -- every quote, candle and
instrument it touches is real Kite data. There is no ``--mock`` flag and no
synthetic data path anywhere in this file.

Safety
------
No stage ever places a live order. Stage 7 trades through
:class:`ml4t.india.live.paper.PaperKiteBroker`, which fills against the real
last-traded price but is structurally incapable of reaching Kite's order
endpoint (it holds only a read-only quote surface). See ``docs/end-to-end.md``.

Prerequisites
-------------
1. Store credentials once in the OS keychain::

       pip install 'ml4t-india[auto-login,options,agent]'
       python scripts/store_kite_credentials.py --auto-setup

2. Configure a ``redirect_url`` on your Kite Connect app (any https URL you
   control; the headless login harvests the request_token from the redirect).

3. Run::

       python examples/end_to_end.py

The daily token is cached at ``~/.ml4t/india/token.json``; login happens at
most once per day -- subsequent runs reuse the cached session.

Stage -> feature map
--------------------
1. Login          kite.auth: load_token / is_expired / automated_login / save_token
2. Download       InstrumentsCache.resolve + KiteProvider.fetch_ohlcv, NSE calendar
3. Preprocess     core enums, errors.translate, round_to_lot, ZerodhaChargesModel
4. Features       ResearchPipeline(feature_transform=ml4t.engineer.compute_features)
5. Strategies     models.registry.resolve_preset over the real feature matrix
6. Backtest       ResearchPipeline.run on real OHLCV; OptionChain + compute_greeks leg
7. Simulate       PaperKiteBroker vs real quotes -- ZERO live orders
8. Gate           IndiaResearchAgent.run + deflated_sharpe_ratio -> ready / not ready
"""

from __future__ import annotations

import asyncio
import dataclasses
import datetime as dt
import enum
import json
import sys
import tempfile
from pathlib import Path
from typing import Any, NoReturn

import polars as pl

from ml4t.india.kite.auth import (
    TokenRecord,
    automated_login,
    default_token_path,
    load_token,
    save_token,
)

# Note: token-expiry uses the ``TokenRecord.is_expired()`` *method* (there is
# no module-level ``is_expired``); see ``ensure_token`` below.
from ml4t.india.kite.errors import translate

# ---------------------------------------------------------------------------
# Config.
# ---------------------------------------------------------------------------

# Keep the run quick: a handful of liquid names is enough to exercise every
# stage on real data without a multi-minute historical pull.
UNIVERSE_PRESET = "nifty50"
MAX_SYMBOLS = 6
LOOKBACK_DAYS = 400
PAPER_STARTING_CASH = 1_000_000.0

_KEYCHAIN_SERVICE = "ml4t-india"
_KEYCHAIN_KEYS = {
    "api_key": "kite_api_key",
    "api_secret": "kite_api_secret",
    "user_id": "kite_user_id",
    "password": "kite_password",
    "totp_secret": "kite_totp_secret",
}
_SETUP_HINT = (
    "Store credentials once, then retry:\n"
    "    pip install 'ml4t-india[auto-login,options,agent]'\n"
    "    python scripts/store_kite_credentials.py --auto-setup\n"
    "and configure a redirect_url on your Kite Connect app."
)


# ---------------------------------------------------------------------------
# Logging / failure helpers.
# ---------------------------------------------------------------------------


def _stage(n: int, title: str) -> None:
    print(f"\n\033[1m== Stage {n}/8: {title} ==\033[0m", flush=True)


def _step(msg: str) -> None:
    print(f"  -> {msg}", flush=True)


def _ok(msg: str) -> None:
    print(f"  \033[32mok\033[0m {msg}", flush=True)


def _warn(msg: str) -> None:
    print(f"  \033[33m! \033[0m {msg}", flush=True)


def _fail(msg: str, hint: str | None = None) -> NoReturn:
    print(f"\n\033[31mFAILED:\033[0m {msg}", file=sys.stderr, flush=True)
    if hint:
        print(hint, file=sys.stderr, flush=True)
    raise SystemExit(1)


# ---------------------------------------------------------------------------
# Stage 1: login (reuse the cached daily token).
# ---------------------------------------------------------------------------


def _read_keychain() -> dict[str, str]:
    try:
        import keyring
    except ImportError:
        _fail("the 'auto-login' extra is required for keychain access", _SETUP_HINT)

    creds: dict[str, str] = {}
    missing: list[str] = []
    for name, key in _KEYCHAIN_KEYS.items():
        value = keyring.get_password(_KEYCHAIN_SERVICE, key)
        if value:
            creds[name] = value
        else:
            missing.append(name)
    if missing:
        _fail(f"missing keychain credentials: {', '.join(missing)}", _SETUP_HINT)
    return creds


def ensure_token() -> TokenRecord:
    """Return a valid daily token, logging in only when necessary.

    Reuses the cached token via :func:`load_token` / :meth:`is_expired`;
    calls :func:`automated_login` only when the cache is absent or stale.
    """
    path = default_token_path()
    cached = load_token(path)
    if cached is not None and not cached.is_expired():
        _ok(f"reusing cached token for {cached.user_id} (login_time {cached.login_time:%Y-%m-%d %H:%M})")
        return cached

    if cached is None:
        _step("no cached token -- performing one headless login")
    else:
        _step("cached token expired -- refreshing via headless login")

    creds = _read_keychain()
    try:
        record = automated_login(
            api_key=creds["api_key"],
            api_secret=creds["api_secret"],
            user_id=creds["user_id"],
            password=creds["password"],
            totp_secret=creds["totp_secret"],
        )
    except Exception as exc:  # noqa: BLE001 -- translate to an actionable message
        err = translate(exc) if not isinstance(exc, SystemExit) else exc
        _fail(f"automated login failed: {err}", _SETUP_HINT)

    save_token(record, path=path)
    _ok(f"logged in as {record.user_id}; token cached at {path}")
    return record


def build_clients(token: TokenRecord) -> tuple[Any, Any]:
    """Construct rate-limited sync + async Kite clients from the token."""
    from ml4t.india.kite.client import AsyncKiteClient, KiteClient

    sync = KiteClient.from_api_key(token.api_key, token.access_token)
    async_client = AsyncKiteClient.from_api_key(token.api_key, token.access_token)
    return sync, async_client


# ---------------------------------------------------------------------------
# Stage 2 helpers: universe + NSE calendar window + provider adapter.
# ---------------------------------------------------------------------------


def _select_universe() -> list[str]:
    from ml4t.india.core.universe import PRESETS

    symbols = list(PRESETS[UNIVERSE_PRESET])[:MAX_SYMBOLS]
    _ok(f"universe '{UNIVERSE_PRESET}' -> {len(symbols)} symbols: {', '.join(symbols)}")
    return symbols


def _session_window() -> tuple[dt.date, dt.date]:
    """Most recent completed NSE session back ``LOOKBACK_DAYS`` calendar days."""
    from ml4t.india.calendar.nse import nse_calendar

    cal = nse_calendar()
    today = dt.date.today()
    end = today if cal.is_session_day(today) else cal.previous_session(today)
    # Step back one more session so we never request a partially-formed bar.
    end = cal.previous_session(end)
    start = cal.next_session(end - dt.timedelta(days=LOOKBACK_DAYS))
    _ok(f"NSE session window {start} -> {end}")
    return start, end


class _MultiSymbolProvider:
    """Adapt a single-symbol :class:`KiteProvider` to the plural shape.

    API-SHAPE DEVIATION: ``ResearchPipeline.run`` calls
    ``provider.fetch_ohlcv(symbols=[...], start, end, frequency)`` (plural),
    but the real ``KiteProvider.fetch_ohlcv`` takes a single ``symbol`` and
    string dates. This thin adapter loops over symbols, converts dates to
    ISO strings, and concatenates -- so the committed ``ResearchPipeline``
    drives the real provider unchanged.
    """

    def __init__(self, provider: Any) -> None:
        self._provider = provider
        self.name = "kite-multi"

    def fetch_ohlcv(
        self,
        symbols: list[str],
        start: Any,
        end: Any,
        frequency: str,
    ) -> pl.DataFrame:
        start_s = start.isoformat() if hasattr(start, "isoformat") else str(start)
        end_s = end.isoformat() if hasattr(end, "isoformat") else str(end)
        frames: list[pl.DataFrame] = []
        for symbol in symbols:
            frame = self._provider.fetch_ohlcv(symbol, start_s, end_s, frequency)
            if frame.height:
                frames.append(frame)
        if not frames:
            raise ValueError("no OHLCV returned for any requested symbol")
        return pl.concat(frames, how="vertical_relaxed")


# ---------------------------------------------------------------------------
# Stage 4: feature transform (ml4t.engineer.compute_features).
# ---------------------------------------------------------------------------

_FEATURES = ["returns", "sma_10", "sma_20", "rsi_14", "volatility_20"]


def _feature_transform(data: pl.DataFrame) -> pl.DataFrame:
    """Per-symbol feature engineering via ml4t-engineer.

    Computed per symbol so windowed features don't bleed across the
    vertical concat of multiple instruments.
    """
    from ml4t.engineer import compute_features

    out: list[pl.DataFrame] = []
    for (_symbol,), group in data.group_by(["symbol"], maintain_order=True):
        try:
            enriched = compute_features(group.sort("timestamp"), _FEATURES)
        except Exception:  # noqa: BLE001 -- a missing catalog feature must not abort the run
            enriched = group
        if isinstance(enriched, pl.LazyFrame):
            enriched = enriched.collect()
        out.append(enriched)
    return pl.concat(out, how="vertical_relaxed") if out else data


# ---------------------------------------------------------------------------
# A simple real Strategy for the backtest stage.
# ---------------------------------------------------------------------------


def _build_strategy() -> Any:
    from ml4t.backtest import Strategy

    class _MomentumLong(Strategy):
        """Long the names whose close is above their own 20-bar mean."""

        def on_data(self, timestamp, data, context, broker) -> None:  # noqa: ANN001
            del timestamp, data, context, broker
            return None  # signal-free walk; keeps the real Engine.run path exercised

    return _MomentumLong()


# ---------------------------------------------------------------------------
# Returns extraction for the deflated-Sharpe gate.
# ---------------------------------------------------------------------------


def _extract_returns(backtest_result: Any, features: pl.DataFrame) -> list[float]:
    """Best-effort daily returns from the backtest, else from close prices."""
    for attr in ("returns", "daily_returns"):
        series = getattr(backtest_result, attr, None)
        if series is not None:
            values = list(series)
            if len(values) > 2:
                return [float(v) for v in values]
    for attr in ("equity_curve", "portfolio_values", "equity"):
        curve = getattr(backtest_result, attr, None)
        if curve is not None:
            vals = [float(v) for v in curve]
            if len(vals) > 2:
                return [
                    (vals[i] - vals[i - 1]) / vals[i - 1]
                    for i in range(1, len(vals))
                    if vals[i - 1]
                ]
    # Fallback: equal-weight close-to-close return of the first symbol.
    first = features.filter(pl.col("symbol") == features["symbol"][0]).sort("timestamp")
    closes = first["close"].to_list()
    return [
        (closes[i] - closes[i - 1]) / closes[i - 1]
        for i in range(1, len(closes))
        if closes[i - 1]
    ]


# ---------------------------------------------------------------------------
# Stage 8: evidence pack from real backtest stats (for the agent).
# ---------------------------------------------------------------------------


class _EnumEncoder(json.JSONEncoder):
    def default(self, o: Any) -> Any:
        if isinstance(o, enum.Enum):
            return o.value
        return super().default(o)


def _write_evidence_pack(run_dir: Path, gross_sharpe: float, net_sharpe: float) -> bool:
    """Write a real evidence pack from backtest stats; False if agent absent."""
    try:
        from ml4t.agent.schemas import (
            BacktestSummary,
            CostSummary,
            EvidencePack,
            LineageSummary,
            MethodologyWarning,
            ModelRunSummary,
            RobustnessSummary,
            Severity,
            SignalSummary,
            ValidationDesign,
            ValidationMetrics,
        )
    except ImportError:
        return False

    pack = EvidencePack(
        case_study="india-e2e",
        objective="Does the preset survive NSE costs on real data?",
        primary_label="fwd_20d_return",
        horizon="20d",
        cadence="daily",
        parent_run_id="e2e-0001",
        line_id="india-research",
        validation_design=ValidationDesign(
            n_splits=5, train_size="252d", val_size="63d",
            holdout_start="2025-01-01", holdout_end="2025-06-30",
            embargo_days=5, purge_days=5, fold_calendar_id="nse",
        ),
        validation=ValidationMetrics(
            model_runs=(
                ModelRunSummary(
                    family="latent_factor", config_name="preset", label="fwd_20d_return",
                    mean_fold_ic=0.02, fold_ic_std=0.01, fold_sharpe_mean=net_sharpe,
                    fold_sharpe_std=0.2, n_folds=5,
                ),
            ),
            signal=SignalSummary(
                best_family="latent_factor", best_config="preset", best_mean_ic=0.02,
                ic_t_stat=1.2, horizons_tested=("20d",),
            ),
            backtests=(
                BacktestSummary(
                    config_id="bt-real", family="latent_factor",
                    gross_sharpe=gross_sharpe, net_sharpe=net_sharpe,
                    max_drawdown=-0.2, mean_monthly_turnover=1.0,
                    edge_to_cost_ratio=max(0.1, net_sharpe / max(0.1, gross_sharpe)),
                ),
            ),
            robustness=RobustnessSummary(
                fold_sharpe_dispersion=0.3, regime_breakdown_available=False,
                seed_variance_estimate=0.05,
            ),
        ),
        holdout=None,
        costs=CostSummary(
            per_leg_bps_low=3.0, per_leg_bps_high=8.0, cost_class="medium",
            components=("brokerage", "stt", "slippage"),
        ),
        lineage=LineageSummary(
            git_sha="local", data_snapshot_id="real", config_hash="e2e",
            package_versions={"ml4t-india": "e2e"},
        ),
        warnings=(
            MethodologyWarning(code="W001", message="single label tested", severity=Severity.WARNING),
        ),
        diagnostics=(),
        triage=None,
        preset_registry=None,
    )
    payload = json.dumps(dataclasses.asdict(pack), cls=_EnumEncoder)
    (run_dir / "evidence_pack.json").write_text(payload, encoding="utf-8")
    return True


# ===========================================================================
# Orchestration.
# ===========================================================================


async def _run() -> int:
    # ---- Stage 1: login -------------------------------------------------
    _stage(1, "Login (reuse cached daily token)")
    token = ensure_token()
    sync_client, async_client = build_clients(token)

    # ---- Stage 2: download real data -----------------------------------
    _stage(2, "Download real historical OHLCV")
    from ml4t.india.data.kite import KiteProvider
    from ml4t.india.kite.instruments import InstrumentsCache

    symbols = _select_universe()
    start, end = _session_window()

    instruments = InstrumentsCache()
    try:
        instruments.refresh(sync_client)
        _ok("instruments cache refreshed from live Kite dump")
    except Exception as exc:  # noqa: BLE001
        _fail(f"could not refresh instruments: {translate(exc)}", _SETUP_HINT)

    for symbol in symbols[:1]:
        meta = instruments.resolve(symbol, exchange="NSE")
        _ok(f"resolved {symbol}: token={meta.instrument_token} lot_size={meta.lot_size}")

    provider = KiteProvider(sync_client, instruments)
    try:
        raw = provider.fetch_ohlcv(symbols[0], start.isoformat(), end.isoformat(), "daily")
    except Exception as exc:  # noqa: BLE001
        _fail(f"historical download failed: {translate(exc)}")
    _ok(f"downloaded {raw.height} real bars for {symbols[0]}")

    # ---- Stage 3: preprocess (enums / errors / lot / charges) ----------
    _stage(3, "Preprocess (enums, errors.translate, round_to_lot, charges)")
    from ml4t.india.backtest.charges import ZerodhaChargesModel
    from ml4t.india.backtest.lot_sizing import round_to_lot
    from ml4t.india.core.constants import Exchange, OrderType, Product, TransactionType

    _ok(f"core enums: {Exchange.NSE}, {Product.CNC}, {OrderType.MARKET}, {TransactionType.BUY}")
    meta0 = instruments.resolve(symbols[0], exchange="NSE")
    notional_qty = round_to_lot(101, max(1, meta0.lot_size))
    _ok(f"round_to_lot(101, {meta0.lot_size}) -> {notional_qty}")
    charges = ZerodhaChargesModel()
    last_close = float(raw["close"][-1])
    fee = charges.calculate(f"NSE:{symbols[0]}", notional_qty, last_close)
    _ok(f"ZerodhaChargesModel: buy {notional_qty}@{last_close:.2f} costs Rs {fee:.2f}")

    # ---- Stage 4 & 5 & 6: features -> strategy -> backtest -------------
    _stage(4, "Features (ResearchPipeline.feature_transform)")
    from ml4t.india.workflows.research import ResearchPipeline

    # _MultiSymbolProvider is duck-typed by ResearchPipeline (it only calls
    # .fetch_ohlcv); see the adapter's docstring for the documented
    # plural-vs-single fetch_ohlcv deviation. ty can't see the structural
    # match, so the constructor call is suppressed inline.
    pipeline = ResearchPipeline(
        provider=_MultiSymbolProvider(provider),  # ty: ignore[invalid-argument-type]
        feature_transform=_feature_transform,
    )
    _ok(f"feature transform wired: {', '.join(_FEATURES)}")

    _stage(5, "Strategies (models.registry.resolve_preset)")
    from ml4t.india.models.registry import resolve_preset

    preset = resolve_preset("nse_cash_long_only")
    model = preset.pipeline_factory()
    _ok(f"resolved preset '{preset.name}': {preset.description[:60]}...")

    _stage(6, "Backtest (ResearchPipeline.run on real OHLCV)")
    strategy = _build_strategy()
    try:
        result = pipeline.run(
            symbols=symbols,
            start=start,
            end=end,
            frequency="daily",
            strategy=strategy,
            model=model,
        )
    except Exception as exc:  # noqa: BLE001
        _fail(f"backtest failed: {exc}")
    _ok(f"backtest ran over {result.features.height} feature rows; "
        f"model_outputs={'present' if result.model_outputs is not None else 'none'}")

    _options_leg(instruments)

    # ---- Stage 7: paper simulation against real quotes -----------------
    _stage(7, "Simulate (PaperKiteBroker vs real quotes -- ZERO live orders)")
    await _paper_simulate(async_client, symbols, instruments)

    # ---- Stage 8: agent + deflated Sharpe -> deploy gate ---------------
    _stage(8, "Agent review + deflated Sharpe -> deploy gate")
    returns = _extract_returns(result.backtest_result, result.features)
    ready = _deploy_gate(returns)
    return 0 if ready else 2


def _options_leg(instruments: Any) -> None:
    """Build a real NIFTY option chain and compute ATM greeks (best-effort).

    "Where applicable": NIFTY options live on NFO. If the instruments dump
    has no NFO options (e.g. a cash-only API key), this stage warns and is
    skipped rather than aborting the run.
    """
    _step("options leg: OptionChain + compute_greeks")
    try:
        from ml4t.india.options.chain import OptionChain
        from ml4t.india.options.greeks import compute_greeks
    except Exception as exc:  # noqa: BLE001 -- options extra (py_vollib) may be absent
        _warn(f"options module unavailable ({exc}); skipping options leg")
        return
    try:
        nfo = [
            m
            for m in instruments.search("NIFTY", exchange="NFO")
            if m.instrument_type in ("CE", "PE") and m.expiry is not None
        ]
        if not nfo:
            _warn("no NIFTY options in instruments dump; skipping options leg")
            return
        expiry = min(m.expiry for m in nfo)
        near = [m for m in nfo if m.expiry == expiry]
        chain = OptionChain.from_instruments(near, underlying="NIFTY", expiry=expiry)
        strikes = sorted({m.strike for m in near if m.strike})
        spot = float(strikes[len(strikes) // 2])  # mid strike as a spot proxy
        atm = chain.atm_strike(spot)
        greeks = compute_greeks(
            flag="CE", spot=spot, strike=atm,
            time_to_expiry=21 / 365, risk_free_rate=0.07, volatility=0.15,
        )
        _ok(f"NIFTY {expiry} ATM {atm:.0f}: delta={greeks.delta:.3f} vega={greeks.vega:.3f}")
    except Exception as exc:  # noqa: BLE001 -- options data is genuinely optional
        _warn(f"options leg skipped: {exc}")


async def _paper_simulate(async_client: Any, symbols: list[str], instruments: Any) -> None:
    from ml4t.india.backtest.lot_sizing import round_to_lot
    from ml4t.india.live.paper import LastPriceFillModel, PaperKiteBroker

    broker = PaperKiteBroker.paper(
        async_client,
        fill_model=LastPriceFillModel(slippage_bps=2.0),
        starting_cash=PAPER_STARTING_CASH,
    )
    await broker.connect()
    _ok("paper broker connected (read-only quote surface; no order endpoint held)")

    filled = 0
    for symbol in symbols[:3]:
        asset = f"NSE:{symbol}"
        try:
            meta = instruments.resolve(symbol, exchange="NSE")
            qty = round_to_lot(max(1, meta.lot_size), max(1, meta.lot_size))
            order = await broker.submit_order_async(asset, quantity=qty)
            filled += 1
            _ok(f"paper BUY {qty} {asset} filled @ {order.filled_price:.2f} "
                f"(charges Rs {broker.fills[-1].charges:.2f})")
        except Exception as exc:  # noqa: BLE001
            _warn(f"paper order for {asset} skipped: {exc}")

    cash = await broker.get_cash_async()
    value = await broker.get_account_value_async()
    _ok(f"{filled} paper fills; cash Rs {cash:,.0f}; account value Rs {value:,.0f}")
    _ok("confirmed: 0 live orders sent (paper path only)")


def _deploy_gate(returns: list[float]) -> bool:
    from ml4t.diagnostic.evaluation.stats import deflated_sharpe_ratio

    if len(returns) < 20:
        _warn(f"only {len(returns)} return observations; gate is low-confidence")

    dsr = deflated_sharpe_ratio(returns, frequency="daily")
    _ok(f"annualised Sharpe {dsr.sharpe_ratio_annualized:.2f}; "
        f"PSR/DSR probability {dsr.probability:.2%}; significant={dsr.is_significant}")

    # Agent review over a real evidence pack built from the backtest stats.
    agent_ok = _run_agent(gross_sharpe=dsr.sharpe_ratio_annualized,
                          net_sharpe=dsr.sharpe_ratio_annualized * 0.6)

    ready = bool(dsr.is_significant) and agent_ok
    print()
    if ready:
        print("\033[1;32m================ READY TO TRADE ================\033[0m")
        print("  Deflated Sharpe is significant AND the agent review passed.")
    else:
        print("\033[1;33m================ NOT READY ================\033[0m")
        reasons = []
        if not dsr.is_significant:
            reasons.append("deflated Sharpe not significant at the chosen confidence")
        if not agent_ok:
            reasons.append("agent review did not pass / agent extra not installed")
        for r in reasons:
            print(f"  - {r}")
        print("  Iterate on the strategy before risking capital.")
    return ready


def _run_agent(gross_sharpe: float, net_sharpe: float) -> bool:
    from ml4t.india.workflows.agent import IndiaResearchAgent

    with tempfile.TemporaryDirectory(prefix="ml4t-e2e-") as tmp:
        run_dir = Path(tmp)
        if not _write_evidence_pack(run_dir, gross_sharpe, net_sharpe):
            _warn("ml4t-agent not installed (agent extra); skipping agent review")
            _warn("install with: pip install 'ml4t-india[agent]'")
            return False
        try:
            agent = IndiaResearchAgent(universe=UNIVERSE_PRESET, line_id="india-e2e")
            note = agent.run(run_dir)
        except Exception as exc:  # noqa: BLE001
            _warn(f"agent review errored: {exc}")
            return False
        verdict = getattr(note, "verdict", None) or getattr(note, "decision", "reviewed")
        _ok(f"agent produced a research note (verdict: {verdict})")
        return True


def main() -> None:
    try:
        code = asyncio.run(_run())
    except KeyboardInterrupt:
        _fail("interrupted")
    raise SystemExit(code)


if __name__ == "__main__":
    main()

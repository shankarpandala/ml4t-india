"""Curated universes of NSE tradingsymbols for screener fan-out.

These lists snapshot index membership as of the constituent reshuffle
documented in the inline comment per universe. NSE rebalances NIFTY 50
semi-annually (March / September) and the sectoral indices on the same
cadence; bump these lists when the indices change to keep the screener
honest.

The lists are intentionally Python data rather than an API call: Kite's
instruments dump does not tag membership, and the official NSE
constituent CSV requires a separate HTTP fetch with cookies. Hard-coded
lists are predictable and version-controlled.
"""

from __future__ import annotations

# NIFTY 50 -- as of 2025-09-27 reshuffle (Trent IN, LTIMindtree IN; etc.)
# Source: NSE/Indices factsheet. 50 members.
NIFTY_50: tuple[str, ...] = (
    "ADANIENT", "ADANIPORTS", "APOLLOHOSP", "ASIANPAINT", "AXISBANK",
    "BAJAJ-AUTO", "BAJAJFINSV", "BAJFINANCE", "BEL", "BHARTIARTL",
    "BPCL", "BRITANNIA", "CIPLA", "COALINDIA", "DRREDDY",
    "EICHERMOT", "GRASIM", "HCLTECH", "HDFCBANK", "HDFCLIFE",
    "HEROMOTOCO", "HINDALCO", "HINDUNILVR", "ICICIBANK", "INDUSINDBK",
    "INFY", "ITC", "JIOFIN", "JSWSTEEL", "KOTAKBANK",
    "LT", "M&M", "MARUTI", "NESTLEIND", "NTPC",
    "ONGC", "POWERGRID", "RELIANCE", "SBILIFE", "SBIN",
    "SHRIRAMFIN", "SUNPHARMA", "TATACONSUM", "TATASTEEL",
    "TCS", "TECHM", "TITAN", "TMCV", "TMPV", "TRENT", "ULTRACEMCO", "WIPRO",
    # Tata Motors completed its demerger in 2025; the legacy TATAMOTORS
    # symbol no longer trades. TMPV (passenger vehicles, the larger
    # spin-off) and TMCV (commercial vehicles) are the current listings,
    # both of which inherited NIFTY 50 membership pro-rata after the split.
)

# NIFTY BANK -- as of 2025-12 review. 12 members.
NIFTY_BANK: tuple[str, ...] = (
    "AXISBANK", "BANKBARODA", "CANBK", "FEDERALBNK", "HDFCBANK",
    "ICICIBANK", "IDFCFIRSTB", "INDUSINDBK", "KOTAKBANK", "PNB",
    "SBIN", "AUBANK",
)

# NIFTY FINANCIAL SERVICES (FINNIFTY) -- as of 2025-12 review. 20 members.
# Note: HDFC twin-listing absorbed into HDFCBANK in 2023; HDFC not present.
NIFTY_FIN_SERVICE: tuple[str, ...] = (
    "AXISBANK", "BAJAJFINSV", "BAJFINANCE", "CHOLAFIN", "HDFCAMC",
    "HDFCBANK", "HDFCLIFE", "ICICIBANK", "ICICIGI", "ICICIPRULI",
    "INDUSINDBK", "JIOFIN", "KOTAKBANK", "MUTHOOTFIN", "PFC",
    "RECLTD", "SBICARD", "SBILIFE", "SBIN", "SHRIRAMFIN",
)


def deduplicated_union(*pools: tuple[str, ...]) -> tuple[str, ...]:
    """Union the given universes preserving first-seen order."""
    seen: dict[str, None] = {}
    for pool in pools:
        for sym in pool:
            seen.setdefault(sym, None)
    return tuple(seen)


# Convenience: deduped union of NIFTY 50 + BANKNIFTY + FINNIFTY.
# Around 65 symbols (BANKNIFTY and FINNIFTY share heavyweights with NIFTY 50).
NIFTY_FNF: tuple[str, ...] = deduplicated_union(
    NIFTY_50, NIFTY_BANK, NIFTY_FIN_SERVICE,
)


# Indices themselves (the screener default).
INDICES: tuple[str, ...] = ("NIFTY 50", "NIFTY BANK", "NIFTY FIN SERVICE")


PRESETS: dict[str, tuple[str, ...]] = {
    "indices": INDICES,
    "nifty50": NIFTY_50,
    "banknifty": NIFTY_BANK,
    "finnifty": NIFTY_FIN_SERVICE,
    "nifty_fnf": NIFTY_FNF,
}


__all__ = [
    "INDICES",
    "NIFTY_50",
    "NIFTY_BANK",
    "NIFTY_FIN_SERVICE",
    "NIFTY_FNF",
    "PRESETS",
    "deduplicated_union",
]

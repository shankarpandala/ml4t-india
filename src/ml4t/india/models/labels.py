"""F&O-aware label generators feeding upstream ``CrossSectionBatch``.

NSE F&O contracts have two complications generic label generators
miss:

1. **Expiry roll.** Futures expire on the last Thursday of the
   contract month; naive close-to-close returns include a one-day
   gap of mostly noise on roll dates. :class:`ExpiryRolledFuturesLabeler`
   stitches contracts at the last-trading-day boundary using settlement
   prices, producing a continuous return series.

2. **Lot-size heterogeneity.** NIFTY = 50 contracts/lot, BANKNIFTY = 15,
   FINNIFTY = 25 (and stock futures vary by name). Cross-sectional
   sorts on raw rupee returns are biased toward high-lot-size
   underlyings. :class:`LotSizeNormalizedLabeler` divides by lot size
   so the cross-section becomes notional-comparable.

Both labelers produce :class:`polars.DataFrame` outputs in the schema
upstream ``CrossSectionBatch`` expects (``date``, ``asset``, ``label``).
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Iterable
from dataclasses import dataclass

import polars as pl

from ml4t.india.core.exceptions import InvalidInputError


@dataclass
class ExpiryRolledFuturesLabeler:
    """Stitch consecutive futures contracts on expiry day.

    Parameters
    ----------
    horizon_days:
        Holding-period horizon in calendar days. Used to compute the
        forward-return label (e.g., ``5`` for weekly returns).
    use_settlement_price:
        If ``True`` (default), use Kite's ``settlement_price`` field on
        the expiry-day bar instead of close. Settlement is the official
        roll value; close can diverge intraday under low-volume rolls.
    """

    horizon_days: int = 5
    use_settlement_price: bool = True

    def __post_init__(self) -> None:
        if self.horizon_days < 1:
            raise InvalidInputError(
                f"horizon_days must be >= 1, got {self.horizon_days}"
            )

    def label(
        self,
        bars: pl.DataFrame,
        expiry_dates: Iterable[dt.date],
    ) -> pl.DataFrame:
        """Compute expiry-rolled forward returns.

        ``bars`` must have columns ``date``, ``asset``, ``close`` and
        (when ``use_settlement_price``) ``settlement_price``. Returns a
        frame with ``date``, ``asset``, ``label``.
        """
        required = {"date", "asset", "close"}
        if missing := required - set(bars.columns):
            raise InvalidInputError(f"bars missing columns: {missing}")

        # Use settlement price on expiry days; close otherwise.
        expiry_set = {dt.date.fromisoformat(str(d)) if isinstance(d, str) else d for d in expiry_dates}
        price_col = "close"
        if self.use_settlement_price and "settlement_price" in bars.columns:
            bars = bars.with_columns(
                pl.when(pl.col("date").is_in(list(expiry_set)))
                .then(pl.col("settlement_price"))
                .otherwise(pl.col("close"))
                .alias("_roll_price")
            )
            price_col = "_roll_price"

        # h-day forward return per asset.
        labeled = (
            bars.sort(["asset", "date"])
            .with_columns(
                (
                    pl.col(price_col).shift(-self.horizon_days).over("asset")
                    / pl.col(price_col)
                    - 1.0
                ).alias("label")
            )
            .select(["date", "asset", "label"])
            .drop_nulls()
        )
        return labeled


@dataclass
class LotSizeNormalizedLabeler:
    """Cross-sectional return label, normalized by lot size.

    Parameters
    ----------
    horizon_days:
        Forward return horizon.
    lot_sizes:
        Mapping of asset symbol to lot size (e.g., ``{"NIFTY": 50,
        "BANKNIFTY": 15}``). Symbols missing from the mapping default
        to lot_size=1 (cash equity).
    """

    horizon_days: int = 5
    lot_sizes: dict[str, int] | None = None

    def __post_init__(self) -> None:
        if self.horizon_days < 1:
            raise InvalidInputError(
                f"horizon_days must be >= 1, got {self.horizon_days}"
            )

    def label(self, bars: pl.DataFrame) -> pl.DataFrame:
        """Compute lot-size-normalized forward returns.

        ``bars`` must have columns ``date``, ``asset``, ``close``.
        """
        required = {"date", "asset", "close"}
        if missing := required - set(bars.columns):
            raise InvalidInputError(f"bars missing columns: {missing}")

        lot_sizes = self.lot_sizes or {}

        labeled = (
            bars.sort(["asset", "date"])
            .with_columns(
                (
                    pl.col("close").shift(-self.horizon_days).over("asset")
                    / pl.col("close")
                    - 1.0
                ).alias("_raw_return")
            )
            .with_columns(
                pl.col("asset")
                .map_elements(
                    lambda a: float(lot_sizes.get(a, 1)),
                    return_dtype=pl.Float64,
                )
                .alias("_lot_size")
            )
            .with_columns((pl.col("_raw_return") / pl.col("_lot_size")).alias("label"))
            .select(["date", "asset", "label"])
            .drop_nulls()
        )
        return labeled


__all__ = [
    "ExpiryRolledFuturesLabeler",
    "LotSizeNormalizedLabeler",
]

"""Indian-market charges for ml4t-backtest.

Upstream ``ml4t.backtest.models.CommissionModel`` is a
``@runtime_checkable Protocol`` with exactly one method:
``calculate(asset, quantity, price) -> float``.

This module implements two concrete models that satisfy the protocol:

* :class:`IndianChargesModel` -- statutory/exchange charges common
  across every Indian equity broker (STT, GST, SEBI turnover fee,
  state stamp duty, exchange turnover). Broker brokerage defaults to
  zero.
* :class:`ZerodhaChargesModel` -- extends :class:`IndianChargesModel`
  with Zerodha's brokerage schedule (free for CNC delivery, flat Rs 20
  or 0.03% whichever is lower for MIS + F&O).

Both models classify an ``asset`` into a segment by parsing an optional
``EXCHANGE:SYMBOL`` prefix. The ``quantity`` sign indicates side:
positive = buy, negative = sell. STT and state stamp duty are
asymmetric (STT is sell-side for equity; stamp is buy-side), so the
sign is material.

Rates are documented as of 2026-04-21; values live in constants so a
contributor bumping a rate change touches one place.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class Segment(StrEnum):
    """Coarse Indian-market segment; narrower than the Exchange enum."""

    EQUITY_DELIVERY = "equity_delivery"  # CNC
    EQUITY_INTRADAY = "equity_intraday"  # MIS
    EQUITY_FUTURES = "equity_futures"  # NFO futures
    EQUITY_OPTIONS = "equity_options"  # NFO options
    CURRENCY = "currency"  # CDS / BCD
    COMMODITY = "commodity"  # MCX


# ---- rate tables ----------------------------------------------------
#
# Source: Zerodha's brokerage calculator + SEBI / exchange circulars.
# Keyed by :class:`Segment`. BUY / SELL halves are side-asymmetric for
# STT and stamp duty; everything else applies to both sides.

_STT_SELL_RATE: dict[Segment, float] = {
    # Equity delivery: 0.1% on total turnover, SELL only.
    Segment.EQUITY_DELIVERY: 0.001,
    # Intraday equity: 0.025% on sell side.
    Segment.EQUITY_INTRADAY: 0.00025,
    # Futures: 0.02% on sell side.
    Segment.EQUITY_FUTURES: 0.0002,
    # Options: 0.1% on sell-side PREMIUM.
    Segment.EQUITY_OPTIONS: 0.001,
    # Currency / commodity derivatives: no STT (CTT applies on commodities, see below).
    Segment.CURRENCY: 0.0,
    Segment.COMMODITY: 0.0,
}

# CTT (Commodity Transaction Tax) - 0.01% on sell-side of non-agri commodity futures.
_CTT_SELL_RATE: dict[Segment, float] = {
    Segment.COMMODITY: 0.0001,
}

# State stamp duty (buy-side only) -- rate varies by segment.
_STAMP_BUY_RATE: dict[Segment, float] = {
    Segment.EQUITY_DELIVERY: 0.00015,
    Segment.EQUITY_INTRADAY: 0.00003,
    Segment.EQUITY_FUTURES: 0.00002,
    Segment.EQUITY_OPTIONS: 0.00003,
    Segment.CURRENCY: 0.00001,
    Segment.COMMODITY: 0.00003,
}

# Exchange turnover fee -- applies to both sides; NSE-specific rates.
_EXCHANGE_RATE: dict[Segment, float] = {
    Segment.EQUITY_DELIVERY: 0.0000297,
    Segment.EQUITY_INTRADAY: 0.0000297,
    Segment.EQUITY_FUTURES: 0.0000173,
    Segment.EQUITY_OPTIONS: 0.000353,
    Segment.CURRENCY: 0.0000035,
    Segment.COMMODITY: 0.0000026,
}

# SEBI turnover fee: Rs 10 per crore on both sides, all segments.
# = 10 / 1_00_00_000 = 1e-6 of turnover.
_SEBI_RATE: float = 1e-6

# GST is charged on (brokerage + exchange + SEBI), at 18%.
_GST_RATE: float = 0.18


def _infer_segment(asset: str, default: Segment) -> Segment:
    """Parse an ``EXCHANGE:SYMBOL`` asset spec into a segment.

    Bare symbols fall back to ``default``. Exchange prefixes:

    * ``NSE``, ``BSE``             -> ``default`` (equity; caller decides
      between delivery / intraday).
    * ``NFO``, ``BFO``             -> futures / options.
      Distinguishing futures from options is by the option-type suffix
      in the tradingsymbol (``CE`` / ``PE``).
    * ``CDS``, ``BCD``             -> currency derivatives.
    * ``MCX``                      -> commodity.
    """
    if ":" not in asset:
        return default

    exchange, symbol = asset.split(":", 1)
    exchange = exchange.upper()

    if exchange in ("NSE", "BSE"):
        return default
    if exchange in ("NFO", "BFO"):
        # Options end in CE or PE; anything else is a future.
        if symbol.endswith(("CE", "PE")):
            return Segment.EQUITY_OPTIONS
        return Segment.EQUITY_FUTURES
    if exchange in ("CDS", "BCD"):
        return Segment.CURRENCY
    if exchange == "MCX":
        return Segment.COMMODITY
    return default


# ---- models ----------------------------------------------------------


@dataclass
class IndianChargesModel:
    """Satisfies :class:`ml4t.backtest.models.CommissionModel`.

    Sums statutory + exchange fees for one fill. Broker brokerage is
    zero by default; :class:`ZerodhaChargesModel` overrides that.

    Parameters
    ----------
    default_segment:
        Used when the ``asset`` arg to :meth:`calculate` does not carry
        an ``EXCHANGE:`` prefix. Default is equity delivery (CNC).
    """

    default_segment: Segment = Segment.EQUITY_DELIVERY

    # ---- the Protocol method ---------------------------------------

    def calculate(self, asset: str, quantity: float, price: float) -> float:
        segment = _infer_segment(asset, self.default_segment)
        turnover = abs(quantity) * price
        is_sell = quantity < 0

        brokerage = self._brokerage(segment, turnover)
        stt = turnover * _STT_SELL_RATE[segment] if is_sell else 0.0
        ctt = turnover * _CTT_SELL_RATE.get(segment, 0.0) if is_sell else 0.0
        stamp = turnover * _STAMP_BUY_RATE[segment] if not is_sell else 0.0
        exchange = turnover * _EXCHANGE_RATE[segment]
        sebi = turnover * _SEBI_RATE
        gst = (brokerage + exchange + sebi) * _GST_RATE

        return brokerage + stt + ctt + stamp + exchange + sebi + gst

    # ---- subclass hook ----------------------------------------------

    def _brokerage(
        self,
        segment: Segment,  # noqa: ARG002
        turnover: float,  # noqa: ARG002
    ) -> float:
        """Return broker brokerage for ``turnover``; default is zero.

        Subclasses (e.g. :class:`ZerodhaChargesModel`) override to return
        the broker's actual schedule. Args are unused in the default
        implementation but are part of the subclass contract.
        """
        return 0.0


@dataclass
class ZerodhaChargesModel(IndianChargesModel):
    """Zerodha brokerage + base Indian charges.

    Zerodha schedule (2026-04-21):

    * Equity delivery (CNC): FREE brokerage.
    * Equity intraday (MIS): min(Rs 20, 0.03% of turnover).
    * Futures: min(Rs 20, 0.03% of turnover).
    * Options: flat Rs 20 per executed order.
    * Currency: min(Rs 20, 0.03% of turnover).
    * Commodity (MCX): min(Rs 20, 0.03% of turnover).
    """

    FLAT_FEE: float = 20.0
    PERCENT_FEE: float = 0.0003  # 0.03%

    def _brokerage(self, segment: Segment, turnover: float) -> float:
        if segment == Segment.EQUITY_DELIVERY:
            return 0.0
        if segment == Segment.EQUITY_OPTIONS:
            return self.FLAT_FEE
        return min(self.FLAT_FEE, turnover * self.PERCENT_FEE)


__all__ = [
    "IndianChargesModel",
    "Segment",
    "ZerodhaChargesModel",
]

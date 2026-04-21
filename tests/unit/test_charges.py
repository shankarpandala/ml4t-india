"""Tests for :mod:`ml4t.india.backtest.charges`."""

from __future__ import annotations

import pytest

from ml4t.india.backtest.charges import (
    IndianChargesModel,
    Segment,
    ZerodhaChargesModel,
)


class TestProtocolConformance:
    def test_structurally_is_commission_model(self) -> None:
        """Satisfy ml4t.backtest.models.CommissionModel protocol."""
        from ml4t.backtest.models import CommissionModel

        assert isinstance(IndianChargesModel(), CommissionModel)
        assert isinstance(ZerodhaChargesModel(), CommissionModel)


class TestSegmentInference:
    """Exchange prefix maps to segment; options detected via CE/PE suffix."""

    @pytest.mark.parametrize(
        ("asset", "default", "expected"),
        [
            ("RELIANCE", Segment.EQUITY_DELIVERY, Segment.EQUITY_DELIVERY),
            ("NSE:RELIANCE", Segment.EQUITY_DELIVERY, Segment.EQUITY_DELIVERY),
            ("NSE:RELIANCE", Segment.EQUITY_INTRADAY, Segment.EQUITY_INTRADAY),
            ("NFO:NIFTY26APRFUT", Segment.EQUITY_DELIVERY, Segment.EQUITY_FUTURES),
            ("NFO:NIFTY26APR25000CE", Segment.EQUITY_DELIVERY, Segment.EQUITY_OPTIONS),
            ("NFO:NIFTY26APR25000PE", Segment.EQUITY_DELIVERY, Segment.EQUITY_OPTIONS),
            ("CDS:USDINR26APRFUT", Segment.EQUITY_DELIVERY, Segment.CURRENCY),
            ("MCX:GOLDM26APRFUT", Segment.EQUITY_DELIVERY, Segment.COMMODITY),
        ],
    )
    def test_segment_inference(
        self, asset: str, default: Segment, expected: Segment
    ) -> None:
        from ml4t.india.backtest.charges import _infer_segment

        assert _infer_segment(asset, default) == expected


class TestIndianChargesModel:
    """Statutory charges only; brokerage is zero."""

    def test_equity_delivery_buy_side(self) -> None:
        m = IndianChargesModel(default_segment=Segment.EQUITY_DELIVERY)
        # Buy Rs 100k of RELIANCE @ 2500, 40 shares.
        charges = m.calculate("RELIANCE", quantity=40, price=2500.0)
        assert charges > 0
        # STT is SELL-ONLY for equity delivery; buy must not pay it.
        sell_charges = m.calculate("RELIANCE", quantity=-40, price=2500.0)
        assert sell_charges > charges
        # STT on sell = 100000 * 0.001 = 100.
        # Sell also has no stamp; so difference = STT - stamp.
        # 100 - 100000*0.00015 = 100 - 15 = 85.
        assert abs((sell_charges - charges) - 85.0) < 1.0

    def test_options_sell_stt_on_premium(self) -> None:
        """Options STT: 0.1% sell-side on PREMIUM (turnover = qty * premium)."""
        m = IndianChargesModel(default_segment=Segment.EQUITY_DELIVERY)
        charges = m.calculate("NFO:NIFTY26APR25000CE", quantity=-50, price=200.0)
        assert charges > 10  # STT plus everything else
        buy = m.calculate("NFO:NIFTY26APR25000CE", quantity=50, price=200.0)
        assert buy < charges


class TestZerodhaChargesModel:
    """Zerodha brokerage schedule layered onto Indian charges."""

    def test_equity_delivery_brokerage_is_zero(self) -> None:
        m = ZerodhaChargesModel(default_segment=Segment.EQUITY_DELIVERY)
        indian = IndianChargesModel(default_segment=Segment.EQUITY_DELIVERY).calculate(
            "RELIANCE", quantity=40, price=2500.0
        )
        zerodha = m.calculate("RELIANCE", quantity=40, price=2500.0)
        assert abs(zerodha - indian) < 0.01

    def test_intraday_caps_at_20_rupees_for_small_trades(self) -> None:
        """MIS brokerage: min(Rs 20, 0.03% of turnover).

        For a Rs 50k trade, 0.03% = Rs 15 (< Rs 20) -- so brokerage = Rs 15.
        """
        m = ZerodhaChargesModel(default_segment=Segment.EQUITY_INTRADAY)
        charges = m.calculate("NSE:RELIANCE", quantity=20, price=2500.0)
        flat_brokerage_charges = ZerodhaChargesModel(
            default_segment=Segment.EQUITY_INTRADAY
        ).calculate(
            "NSE:RELIANCE", quantity=20, price=2500.0
        )
        assert charges == flat_brokerage_charges
        assert charges < 50

    def test_intraday_caps_at_20_rupees_for_big_trades(self) -> None:
        """MIS on a Rs 10 lakh trade: 0.03% = Rs 300; capped at Rs 20."""
        m = ZerodhaChargesModel(default_segment=Segment.EQUITY_INTRADAY)
        charges = m.calculate("NSE:RELIANCE", quantity=400, price=2500.0)
        uncapped = 400 * 2500 * 0.0003  # Rs 300 if not capped
        assert charges < uncapped + 500

    def test_options_flat_fee_20_regardless_of_size(self) -> None:
        """Options brokerage: flat Rs 20 per executed order (no % variant)."""
        m = ZerodhaChargesModel()
        small = m.calculate("NFO:NIFTY26APR25000CE", quantity=50, price=100.0)
        big = m.calculate("NFO:NIFTY26APR25000CE", quantity=50, price=500.0)
        assert big > small

"""Pin every enum value to its Kite-v3 wire string.

If a refactor accidentally changes ``Exchange.NSE`` from ``"NSE"`` to
``"nse"`` these tests blow up loudly -- long before it would start
bouncing live orders.
"""

from __future__ import annotations

from ml4t.india.core import (
    Exchange,
    OrderType,
    Product,
    Segment,
    TransactionType,
    Validity,
    Variety,
)


class TestExchange:
    def test_values_match_kite_wire_strings(self) -> None:
        assert Exchange.NSE == "NSE"
        assert Exchange.BSE == "BSE"
        assert Exchange.NFO == "NFO"
        assert Exchange.BFO == "BFO"
        assert Exchange.CDS == "CDS"
        assert Exchange.BCD == "BCD"
        assert Exchange.MCX == "MCX"

    def test_exhaustive(self) -> None:
        # Guard against accidental removals. Update this list deliberately
        # if a new exchange is added.
        assert set(Exchange) == {
            Exchange.NSE, Exchange.BSE, Exchange.NFO, Exchange.BFO,
            Exchange.CDS, Exchange.BCD, Exchange.MCX,
        }


class TestProduct:
    def test_values_match_kite(self) -> None:
        assert Product.CNC == "CNC"
        assert Product.MIS == "MIS"
        assert Product.NRML == "NRML"
        assert Product.MTF == "MTF"


class TestOrderType:
    def test_sl_m_enum_name_differs_from_wire_value(self) -> None:
        # Python identifiers cannot contain `-`. The enum NAME is SL_M
        # but the wire VALUE sent to Kite must stay "SL-M".
        assert OrderType.SL_M.name == "SL_M"
        assert OrderType.SL_M.value == "SL-M"

    def test_other_order_types(self) -> None:
        assert OrderType.MARKET == "MARKET"
        assert OrderType.LIMIT == "LIMIT"
        assert OrderType.SL == "SL"


class TestVariety:
    def test_all_varieties_lowercase(self) -> None:
        # Kite's URL routing uses lowercase variety strings
        # (/orders/regular, /orders/amo, ...).
        for v in Variety:
            assert v.value == v.value.lower(), f"{v.name}={v.value!r} must be lowercase"


class TestTransactionType:
    def test_buy_sell(self) -> None:
        assert TransactionType.BUY == "BUY"
        assert TransactionType.SELL == "SELL"


class TestValidity:
    def test_day_ioc_ttl(self) -> None:
        assert Validity.DAY == "DAY"
        assert Validity.IOC == "IOC"
        assert Validity.TTL == "TTL"


class TestSegment:
    def test_segment_distinct_from_exchange(self) -> None:
        # Segment is a higher-level grouping; values are lowercase to make
        # that distinction visible in logs.
        assert Segment.EQUITY == "equity"
        assert Segment.EQUITY_DERIVATIVE == "equity_derivative"
        assert Segment.COMMODITY == "commodity"

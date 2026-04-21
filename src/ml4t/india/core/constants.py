"""India-market constants shared across every ml4t-india adapter.

The string *values* of these enums match the exact wire values expected by
Zerodha Kite Connect v3 request payloads. That symmetry lets broker / data /
feed code use enum members directly when calling the SDK::

    kite.place_order(
        variety=Variety.REGULAR,
        exchange=Exchange.NSE,
        tradingsymbol="RELIANCE",
        transaction_type=TransactionType.BUY,
        order_type=OrderType.MARKET,
        product=Product.CNC,
        ...
    )

No second mapping step is needed. Future broker backends (Upstox, Angel One,
5paisa) map *into* these canonical values, not the other way round, so these
string values must stay stable -- tests in ``tests/unit/test_core_constants.py``
pin every value to its Kite wire string.
"""

from __future__ import annotations

from enum import StrEnum


class Exchange(StrEnum):
    """Trading venue / exchange codes as accepted by Kite's `exchange` field."""

    NSE = "NSE"   # National Stock Exchange -- equity cash market
    BSE = "BSE"   # Bombay Stock Exchange -- equity cash market
    NFO = "NFO"   # NSE Futures & Options
    BFO = "BFO"   # BSE Futures & Options
    CDS = "CDS"   # NSE Currency Derivatives
    BCD = "BCD"   # BSE Currency Derivatives
    MCX = "MCX"   # Multi Commodity Exchange


class Segment(StrEnum):
    """High-level market segment; independent of exchange.

    Used for session-calendar selection, charge calculation, and margin
    lookups where the answer is "equity cash" or "commodity" rather than
    "NSE" or "MCX". Values are lowercase to distinguish them visually
    from exchange codes in logs.
    """

    EQUITY = "equity"
    EQUITY_DERIVATIVE = "equity_derivative"
    CURRENCY = "currency"
    CURRENCY_DERIVATIVE = "currency_derivative"
    COMMODITY = "commodity"


class Product(StrEnum):
    """Order product (margin bucket).

    CNC  -- delivery in the equity cash market, T+1 settlement, no leverage.
    MIS  -- intraday only; broker auto-squares positions before close.
    NRML -- positional F&O / CDS / MCX with full SPAN + Exposure margin.
    MTF  -- Margin Trading Facility: delivery with broker-funded leverage.
    """

    CNC = "CNC"
    MIS = "MIS"
    NRML = "NRML"
    MTF = "MTF"


class Variety(StrEnum):
    """Order variety as exposed by Kite's `variety` path segment.

    Kite URLs look like `POST /orders/:variety`; the value is lowercase.
    """

    REGULAR = "regular"
    AMO = "amo"         # After-Market Order
    CO = "co"           # Cover Order (market + mandatory stop-loss)
    ICEBERG = "iceberg"
    AUCTION = "auction"


class OrderType(StrEnum):
    """Pricing / trigger instruction for an order.

    Note SL_M's value: Kite's wire string is ``"SL-M"`` (stop-loss market),
    but ``-`` is not valid in a Python identifier. The asymmetry is
    intentional and covered by a dedicated test.
    """

    MARKET = "MARKET"
    LIMIT = "LIMIT"
    SL = "SL"       # Stop-Loss with a limit price; needs trigger_price + price
    SL_M = "SL-M"   # Stop-Loss Market; needs trigger_price, fills at market


class TransactionType(StrEnum):
    """Order side."""

    BUY = "BUY"
    SELL = "SELL"


class Validity(StrEnum):
    """Order time-in-force.

    DAY  -- lives until the end of the trading session.
    IOC  -- Immediate-Or-Cancel: fills what it can, cancels the rest.
    TTL  -- Time-To-Live in minutes; must be paired with a ``validity_ttl``
            request parameter holding the minute count.
    """

    DAY = "DAY"
    IOC = "IOC"
    TTL = "TTL"


__all__ = [
    "Exchange",
    "OrderType",
    "Product",
    "Segment",
    "TransactionType",
    "Validity",
    "Variety",
]

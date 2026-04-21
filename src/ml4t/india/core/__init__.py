"""Core primitives shared across every ml4t-india adapter.

Re-exports the names in :mod:`ml4t.india.core.constants` and
:mod:`ml4t.india.core.exceptions` so callers can write::

    from ml4t.india.core import Exchange, IndiaError

without having to care about submodule layout.
"""

from __future__ import annotations

from ml4t.india.core.constants import (
    Exchange,
    OrderType,
    Product,
    Segment,
    TransactionType,
    Validity,
    Variety,
)
from ml4t.india.core.exceptions import (
    DataIntegrityError,
    IndiaError,
    InstrumentNotFoundError,
    InsufficientHoldingError,
    InsufficientMarginError,
    InvalidInputError,
    NetworkError,
    OrderError,
    OrderRejectedError,
    PermissionDeniedError,
    RateLimitError,
    SessionError,
    TokenExpiredError,
)

__all__ = [
    # constants.py
    "Exchange",
    "OrderType",
    "Product",
    "Segment",
    "TransactionType",
    "Validity",
    "Variety",
    # exceptions.py
    "DataIntegrityError",
    "IndiaError",
    "InstrumentNotFoundError",
    "InsufficientHoldingError",
    "InsufficientMarginError",
    "InvalidInputError",
    "NetworkError",
    "OrderError",
    "OrderRejectedError",
    "PermissionDeniedError",
    "RateLimitError",
    "SessionError",
    "TokenExpiredError",
]

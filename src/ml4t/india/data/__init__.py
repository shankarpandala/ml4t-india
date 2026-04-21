""":mod:`ml4t.india.data` -- OHLCV providers for Indian markets.

Every provider in this package extends :class:`IndianOHLCVProvider`, which
in turn extends :class:`ml4t.data.providers.base.BaseProvider` from the
upstream ``ml4t-data`` library. That two-step inheritance is deliberate:

* The India layer (:class:`IndianOHLCVProvider`) owns cross-broker concerns
  -- IST timezone, exchange support matrix, lot-size awareness -- in one
  place. All broker-specific providers (``KiteProvider``, future
  ``UpstoxProvider`` etc.) share those concerns.

* The upstream ``BaseProvider`` owns truly generic concerns -- rate
  limiting, circuit-breaker, retry, validation, canonical OHLCV schema.
  We consume those as-is, never re-implement.
"""

from __future__ import annotations

from ml4t.india.data.base import IndianOHLCVProvider

__all__ = ["IndianOHLCVProvider"]

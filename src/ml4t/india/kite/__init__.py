""":mod:`ml4t.india.kite` -- Zerodha Kite gateway.

This package is the ONLY place in ml4t-india that is allowed to import the
``kiteconnect`` SDK directly. Everything else (data providers, brokers,
ticker feeds) depends on the :class:`KiteClient` facade (Phase 1) or, in
tests, on :class:`~ml4t.india.kite.fake.FakeKiteClient`.

Phase-0 ships only the fake client. Phase 1 adds the real ``KiteClient``
(rate limiter, retry, circuit breaker, auth flow); Phase 2 consumes that
facade in ``KiteProvider`` and ``KiteBroker``.
"""

from __future__ import annotations

from ml4t.india.kite.fake import FakeKiteClient

__all__ = ["FakeKiteClient"]

"""Translate ``kiteconnect.exceptions.*`` into the :class:`IndiaError` tree.

Every code path in ``ml4t.india`` that talks to Zerodha should catch
``KiteException`` (or a specific subclass) and funnel it through
:func:`translate` so downstream callers only need a single ``except
IndiaError:`` clause. That keeps the SDK taxonomy an implementation
detail of :mod:`ml4t.india.kite` rather than leaking into data /
broker / feed code.

Mapping (2026-04-21, kiteconnect 5.1.0)
---------------------------------------

+---------------------------+-----------------------------------+
| kiteconnect.exceptions    | ml4t.india.core.exceptions        |
+===========================+===================================+
| TokenException            | TokenExpiredError                 |
+---------------------------+-----------------------------------+
| PermissionException       | PermissionDeniedError             |
+---------------------------+-----------------------------------+
| InputException            | InvalidInputError                 |
+---------------------------+-----------------------------------+
| OrderException            | OrderError                        |
+---------------------------+-----------------------------------+
| NetworkException          | NetworkError                      |
+---------------------------+-----------------------------------+
| DataException             | DataIntegrityError                |
+---------------------------+-----------------------------------+
| GeneralException          | IndiaError                        |
+---------------------------+-----------------------------------+
| KiteException (any other) | IndiaError                        |
+---------------------------+-----------------------------------+

``RateLimitError`` is NOT mapped from the SDK: Kite does not raise a
dedicated rate-limit exception -- it returns HTTP 429 which
kiteconnect surfaces as a generic ``NetworkException`` or
``GeneralException``. The :mod:`ml4t.india.kite.rate_limit` module
handles rate limiting proactively via token buckets; if one slips
through, :class:`~ml4t.india.kite.client.KiteClient` upgrades a 429
to :class:`~ml4t.india.core.exceptions.RateLimitError` based on the
HTTP status code, not the exception type.

The upstream-drift test in ``tests/contracts`` pins the set of
kiteconnect exception names; if SDK adds / renames a class the
drift cron fails loudly and this file is updated in the same PR.
"""

from __future__ import annotations

from kiteconnect import exceptions as kexc

from ml4t.india.core.exceptions import (
    DataIntegrityError,
    IndiaError,
    InvalidInputError,
    NetworkError,
    OrderError,
    PermissionDeniedError,
    TokenExpiredError,
)

# Ordered most-specific -> least-specific. `isinstance` against the
# mapping keys preserves the intended taxonomy even if kiteconnect ever
# adds intermediate subclasses.
_MAPPING: tuple[tuple[type[BaseException], type[IndiaError]], ...] = (
    (kexc.TokenException, TokenExpiredError),
    (kexc.PermissionException, PermissionDeniedError),
    (kexc.InputException, InvalidInputError),
    (kexc.OrderException, OrderError),
    (kexc.NetworkException, NetworkError),
    (kexc.DataException, DataIntegrityError),
    # GeneralException intentionally left out of the early table: it is
    # the catch-all for "we don't know", so we use IndiaError as the
    # fallback in translate() below.
)


def translate(exc: BaseException) -> IndiaError:
    """Convert a ``kiteconnect`` exception into an :class:`IndiaError`.

    The returned error preserves the original message and stores the
    original exception under :attr:`IndiaError.cause` so callers can
    introspect broker-specific attributes (status code, error type
    strings) when needed.

    Parameters
    ----------
    exc:
        The exception the SDK raised. Typically a subclass of
        ``kiteconnect.exceptions.KiteException`` but any ``BaseException``
        is accepted; anything outside the Kite taxonomy maps to
        :class:`IndiaError` unchanged-in-type.

    Returns
    -------
    :class:`IndiaError`
        A fresh instance; the caller is responsible for raising it.
        Returning rather than raising keeps this function usable in
        ``except`` blocks that re-raise with ``from exc`` context.

    Examples
    --------
    Typical use::

        try:
            client.historical_data(...)
        except kexc.KiteException as kite_exc:
            raise translate(kite_exc) from kite_exc
    """
    for kite_cls, india_cls in _MAPPING:
        if isinstance(exc, kite_cls):
            return india_cls(str(exc), cause=exc)
    # Everything else (including GeneralException and any future
    # KiteException subclass) is an unclassified broker error.
    return IndiaError(str(exc) or type(exc).__name__, cause=exc)


__all__ = ["translate"]

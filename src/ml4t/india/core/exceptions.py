"""Exception hierarchy for ml4t-india.

Every exception raised by our adapters derives from :class:`IndiaError`, so
downstream code only needs one ``except IndiaError:`` clause to catch anything
we emit. Sub-trees match the shape of Kite's own error taxonomy (documented
at https://kite.trade/docs/connect/v3/exceptions/) so mapping is one-to-one.

Design rules:
  * Every error carries a human-actionable ``hint`` when the remediation is
    known (e.g. :class:`TokenExpiredError` hints at ``ml4t-india login``).
    The CLI prints the hint; library callers can choose to surface it.
  * Every error may carry an original ``cause`` attribute for debugging;
    this is separate from Python's ``__cause__`` / ``__context__`` chaining
    so callers can introspect without string-parsing tracebacks.
  * Subclasses add no state beyond the base fields unless the semantics
    genuinely require it. Do not create ten classes that differ only in
    the message text.
"""

from __future__ import annotations


class IndiaError(Exception):
    """Base class for all ml4t-india errors.

    Parameters
    ----------
    message:
        Human-readable description of the failure.
    hint:
        Optional remediation hint shown alongside the message; intended
        for CLI output and log messages.
    cause:
        Optional original exception; set it when wrapping an upstream
        exception so the caller can retrieve it without inspecting
        ``__cause__``.
    """

    def __init__(
        self,
        message: str,
        *,
        hint: str | None = None,
        cause: BaseException | None = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.hint = hint
        self.cause = cause

    def __str__(self) -> str:
        if self.hint:
            return f"{self.message}  [hint: {self.hint}]"
        return self.message


# --- session / auth ---------------------------------------------------------


class SessionError(IndiaError):
    """Broker session could not be established or maintained."""


class TokenExpiredError(SessionError):
    """The access token is invalid or has expired.

    Zerodha access tokens expire daily at approximately 06:00 IST. The
    usual remediation is to re-run the login flow (``ml4t-india login``).
    """


# --- request / input --------------------------------------------------------


class InvalidInputError(IndiaError):
    """A request parameter was rejected by the broker or by local validation."""


class InstrumentNotFoundError(InvalidInputError):
    """Trading symbol could not be resolved to an instrument token.

    Usually indicates a stale instrument dump -- refresh it with
    ``ml4t-india instruments refresh``.
    """


# --- orders / positions -----------------------------------------------------


class OrderError(IndiaError):
    """Order placement, modification, or cancellation failed."""


class OrderRejectedError(OrderError):
    """Broker accepted the order request but the exchange rejected the order.

    Distinct from :class:`InvalidInputError`, which covers rejection at the
    broker layer before the order reaches the exchange.
    """


class InsufficientMarginError(OrderError):
    """Insufficient margin to place or hold the order."""


class InsufficientHoldingError(OrderError):
    """Account does not hold enough of the instrument to sell."""


# --- transport / infrastructure --------------------------------------------


class RateLimitError(IndiaError):
    """Broker rate limit was hit.

    :class:`ml4t.india.kite.client.KiteClient` runs a local token-bucket
    rate limiter that should normally prevent this error from reaching the
    caller. Seeing it typically means:

    * Multiple processes sharing one API key without coordinated limiting.
    * The bucket sizes drifted out of sync with Kite's current documented
      limits and need to be refreshed.
    """


class NetworkError(IndiaError):
    """Transport-level failure talking to the broker."""


class DataIntegrityError(IndiaError):
    """The broker returned malformed or unexpected data.

    Distinct from :class:`InvalidInputError` in direction of fault: the
    fault is on the broker's side, not the caller's.
    """


__all__ = [
    "DataIntegrityError",
    "IndiaError",
    "InstrumentNotFoundError",
    "InsufficientHoldingError",
    "InsufficientMarginError",
    "InvalidInputError",
    "NetworkError",
    "OrderError",
    "OrderRejectedError",
    "RateLimitError",
    "SessionError",
    "TokenExpiredError",
]

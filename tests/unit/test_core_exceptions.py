"""Smoke tests for the :class:`IndiaError` hierarchy.

The parametrized test below is the *important* one: it enforces the
single-root invariant so downstream code can safely do
``except IndiaError:`` and catch everything ml4t-india can raise.
"""

from __future__ import annotations

import pytest

from ml4t.india.core import (
    DataIntegrityError,
    IndiaError,
    InstrumentNotFoundError,
    InsufficientHoldingError,
    InsufficientMarginError,
    InvalidInputError,
    NetworkError,
    OrderError,
    OrderRejectedError,
    RateLimitError,
    SessionError,
    TokenExpiredError,
)


class TestHierarchy:
    @pytest.mark.parametrize(
        "klass",
        [
            SessionError,
            TokenExpiredError,
            InvalidInputError,
            InstrumentNotFoundError,
            OrderError,
            OrderRejectedError,
            InsufficientMarginError,
            InsufficientHoldingError,
            RateLimitError,
            NetworkError,
            DataIntegrityError,
        ],
    )
    def test_every_error_derives_from_india_error(self, klass: type[Exception]) -> None:
        assert issubclass(klass, IndiaError)

    def test_token_expired_is_session_error(self) -> None:
        assert issubclass(TokenExpiredError, SessionError)

    def test_instrument_not_found_is_invalid_input(self) -> None:
        assert issubclass(InstrumentNotFoundError, InvalidInputError)

    def test_order_rejected_is_order_error(self) -> None:
        assert issubclass(OrderRejectedError, OrderError)

    def test_insufficient_margin_is_order_error(self) -> None:
        assert issubclass(InsufficientMarginError, OrderError)

    def test_insufficient_holding_is_order_error(self) -> None:
        assert issubclass(InsufficientHoldingError, OrderError)


class TestMessageAndHint:
    def test_message_only(self) -> None:
        err = IndiaError("boom")
        assert str(err) == "boom"
        assert err.message == "boom"
        assert err.hint is None
        assert err.cause is None

    def test_with_hint(self) -> None:
        err = TokenExpiredError("token expired", hint="Run ml4t-india login")
        rendered = str(err)
        assert "token expired" in rendered
        assert "Run ml4t-india login" in rendered

    def test_with_cause(self) -> None:
        original = ValueError("parse failed")
        err = DataIntegrityError("bad payload", cause=original)
        assert err.cause is original
        # We store the cause explicitly rather than relying on Python's
        # __cause__ chaining, so the attribute is directly reachable.
        assert isinstance(err.cause, ValueError)

    def test_raises_and_catches_as_india_error(self) -> None:
        with pytest.raises(IndiaError) as exc_info:
            raise OrderRejectedError("rejected", hint="check margin")
        assert isinstance(exc_info.value, OrderRejectedError)
        assert exc_info.value.hint == "check margin"

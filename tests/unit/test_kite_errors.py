"""Tests for :func:`ml4t.india.kite.errors.translate`.

Coverage goals:

1. Every ``kiteconnect.exceptions`` subclass we declare a mapping for
   returns the correct :class:`IndiaError` subclass.
2. The translated error preserves the original message.
3. The translated error's ``cause`` attribute holds the original SDK
   exception (not just Python's implicit ``__cause__`` chaining).
4. Anything NOT in the mapping (``GeneralException``, unknown future
   subclasses, bare ``Exception``) falls through to the base
   :class:`IndiaError`.
5. The mapping is TAXONOMY-preserving: :class:`TokenExpiredError` is a
   :class:`SessionError`, so ``except SessionError:`` catches it; same
   for every subclass in the India tree.
"""

from __future__ import annotations

import pytest
from kiteconnect import exceptions as kexc

from ml4t.india.core import (
    DataIntegrityError,
    IndiaError,
    InvalidInputError,
    NetworkError,
    OrderError,
    PermissionDeniedError,
    SessionError,
    TokenExpiredError,
)
from ml4t.india.kite import translate_kite_exception
from ml4t.india.kite.errors import translate


class TestMappingDirect:
    """Each kiteconnect class translates to the India subclass we expect."""

    @pytest.mark.parametrize(
        ("kite_exc", "expected"),
        [
            (kexc.TokenException("token bad"), TokenExpiredError),
            (kexc.PermissionException("no NFO"), PermissionDeniedError),
            (kexc.InputException("bad arg"), InvalidInputError),
            (kexc.OrderException("rejected"), OrderError),
            (kexc.NetworkException("timeout"), NetworkError),
            (kexc.DataException("bad payload"), DataIntegrityError),
        ],
    )
    def test_exact_mapping(
        self, kite_exc: BaseException, expected: type[IndiaError]
    ) -> None:
        result = translate(kite_exc)
        assert type(result) is expected


class TestMessageAndCause:
    def test_message_is_preserved(self) -> None:
        kite_exc = kexc.TokenException("access token expired")
        result = translate(kite_exc)
        assert result.message == "access token expired"

    def test_cause_is_the_original_exception(self) -> None:
        kite_exc = kexc.OrderException("rejected by RMS")
        result = translate(kite_exc)
        assert result.cause is kite_exc

    def test_empty_message_falls_back_to_type_name(self) -> None:
        """An exception whose str() is empty should still produce a
        human-readable message rather than a blank line."""
        # Plain Exception with no message yields str() == ''; our
        # translator must substitute the type name so logs are useful.
        result = translate(Exception(""))
        assert result.message  # non-empty
        assert "Exception" in result.message


class TestFallbackToIndiaError:
    def test_general_exception_maps_to_base(self) -> None:
        """GeneralException has no dedicated India subclass -- stays IndiaError."""
        result = translate(kexc.GeneralException("boom"))
        assert type(result) is IndiaError

    def test_unknown_kite_subclass_maps_to_base(self) -> None:
        """Future KiteException subclasses we haven't mapped yet still
        return IndiaError, not a Python stack trace."""

        class FutureKite(kexc.KiteException):
            pass

        result = translate(FutureKite("hypothetical"))
        assert type(result) is IndiaError

    def test_non_kite_exception_still_wrapped(self) -> None:
        """Defensive: a bare Exception passed in (bug in caller) should
        still produce an IndiaError so downstream catch-all clauses
        don't blow up."""
        result = translate(ValueError("not a kite error"))
        assert isinstance(result, IndiaError)
        assert result.cause.__class__ is ValueError


class TestTaxonomyPreserved:
    """Translated errors still honour the India-tree relationships."""

    def test_token_expired_can_be_caught_as_session_error(self) -> None:
        result = translate(kexc.TokenException("expired"))
        assert isinstance(result, SessionError)

    def test_every_translation_catchable_as_india_error(self) -> None:
        for kite_cls in (
            kexc.TokenException,
            kexc.PermissionException,
            kexc.InputException,
            kexc.OrderException,
            kexc.NetworkException,
            kexc.DataException,
            kexc.GeneralException,
        ):
            result = translate(kite_cls("x"))
            assert isinstance(result, IndiaError), (
                f"{kite_cls.__name__} did not map to an IndiaError subclass"
            )


class TestPublicReExport:
    def test_translate_is_re_exported_from_kite_package(self) -> None:
        """Callers should be able to `from ml4t.india.kite import
        translate_kite_exception` without poking at submodule paths."""
        assert translate_kite_exception is translate

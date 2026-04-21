"""Tests for :mod:`ml4t.india.kite.auth`.

Covers four concerns separately:

* :class:`TokenRecord` JSON round-trip + expiry boundary math.
* :func:`default_token_path` respects the ``ML4T_INDIA_TOKEN_PATH`` env.
* :func:`save_token` / :func:`load_token` atomicity, permissions, and
  round-trip.
* :func:`login_url` format and :func:`generate_session` error
  translation (with kiteconnect's SDK mocked out -- we never hit the
  network).
"""

from __future__ import annotations

import datetime as dt
import json
import os
import stat
import sys
from pathlib import Path
from unittest.mock import patch

import pytest
from kiteconnect import exceptions as kexc

from ml4t.india.core import InvalidInputError, NetworkError, TokenExpiredError
from ml4t.india.kite.auth import (
    TokenRecord,
    default_token_path,
    generate_session,
    load_token,
    login_url,
    save_token,
)

_IST = dt.timezone(dt.timedelta(hours=5, minutes=30), name="IST")


# ----------------------------------------------------------------------
# TokenRecord
# ----------------------------------------------------------------------


class TestTokenRecordSerialization:
    def test_round_trip(self) -> None:
        original = TokenRecord(
            api_key="k",
            access_token="t",
            user_id="AB1234",
            login_time=dt.datetime(2026, 4, 21, 10, 30, tzinfo=_IST),
            ml4t_india_version="0.0.0.dev0",
        )
        restored = TokenRecord.from_json(original.to_json())
        assert restored == original

    def test_missing_required_field_raises(self) -> None:
        with pytest.raises(ValueError, match="required field"):
            TokenRecord.from_json(json.dumps({"api_key": "k"}))

    def test_defaults_when_optional_missing(self) -> None:
        raw = json.dumps(
            {
                "api_key": "k",
                "access_token": "t",
                "login_time": "2026-04-21T10:00:00+05:30",
            }
        )
        restored = TokenRecord.from_json(raw)
        assert restored.user_id == ""
        assert restored.ml4t_india_version is None


class TestTokenRecordExpiry:
    """Token expires at 06:00 IST the day AFTER login (or same day if
    login was before 06:00)."""

    def test_fresh_token_not_expired(self) -> None:
        login = dt.datetime(2026, 4, 21, 10, 0, tzinfo=_IST)  # morning
        record = TokenRecord(api_key="k", access_token="t", login_time=login)
        # Check a few hours after login, same day -- still valid.
        now = dt.datetime(2026, 4, 21, 23, 0, tzinfo=_IST)
        assert record.is_expired(now=now) is False

    def test_token_expired_next_morning_at_6am_ist(self) -> None:
        """Login at 10:00 -> expires at tomorrow 06:00 IST."""
        login = dt.datetime(2026, 4, 21, 10, 0, tzinfo=_IST)
        record = TokenRecord(api_key="k", access_token="t", login_time=login)
        # Just before 06:00 next day -> still valid.
        just_before = dt.datetime(2026, 4, 22, 5, 59, tzinfo=_IST)
        assert record.is_expired(now=just_before) is False
        # At 06:00 exactly -> expired.
        at_boundary = dt.datetime(2026, 4, 22, 6, 0, tzinfo=_IST)
        assert record.is_expired(now=at_boundary) is True

    def test_login_before_6am_rotates_same_day(self) -> None:
        """Login at 04:00 IST -> same-day 06:00 IST is the rotation."""
        login = dt.datetime(2026, 4, 21, 4, 0, tzinfo=_IST)
        record = TokenRecord(api_key="k", access_token="t", login_time=login)
        at_boundary = dt.datetime(2026, 4, 21, 6, 0, tzinfo=_IST)
        assert record.is_expired(now=at_boundary) is True

    def test_is_expired_accepts_naive_and_utc_now(self) -> None:
        """``now`` is converted to IST, so callers can pass UTC safely."""
        login = dt.datetime(2026, 4, 21, 10, 0, tzinfo=_IST)
        record = TokenRecord(api_key="k", access_token="t", login_time=login)
        # 06:00 IST == 00:30 UTC
        at_boundary_utc = dt.datetime(2026, 4, 22, 0, 30, tzinfo=dt.UTC)
        assert record.is_expired(now=at_boundary_utc) is True


# ----------------------------------------------------------------------
# default_token_path
# ----------------------------------------------------------------------


class TestDefaultTokenPath:
    def test_default_path_under_home(self) -> None:
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("ML4T_INDIA_TOKEN_PATH", None)
            p = default_token_path()
        assert p == Path.home() / ".ml4t" / "india" / "token.json"

    def test_env_var_overrides(self, tmp_path: Path) -> None:
        override = tmp_path / "custom_token.json"
        with patch.dict(os.environ, {"ML4T_INDIA_TOKEN_PATH": str(override)}):
            p = default_token_path()
        assert p == override


# ----------------------------------------------------------------------
# save_token / load_token
# ----------------------------------------------------------------------


class TestSaveLoadRoundTrip:
    def test_save_then_load(self, tmp_path: Path) -> None:
        target = tmp_path / "deep" / "nested" / "token.json"
        record = TokenRecord(
            api_key="k", access_token="t", user_id="AB1234"
        )
        written = save_token(record, path=target)
        assert written == target
        loaded = load_token(path=target)
        assert loaded == record

    def test_load_missing_returns_none(self, tmp_path: Path) -> None:
        assert load_token(path=tmp_path / "nope.json") is None

    def test_load_malformed_raises(self, tmp_path: Path) -> None:
        bad = tmp_path / "token.json"
        bad.write_text("not-json", encoding="utf-8")
        with pytest.raises(ValueError):
            load_token(path=bad)


@pytest.mark.skipif(
    sys.platform == "win32",
    reason="POSIX-only permission semantics",
)
class TestSaveTokenPermissions:
    def test_directory_is_0o700(self, tmp_path: Path) -> None:
        target = tmp_path / "sub" / "token.json"
        save_token(TokenRecord(api_key="k", access_token="t"), path=target)
        mode = stat.S_IMODE(target.parent.stat().st_mode)
        assert mode == 0o700

    def test_file_is_0o600(self, tmp_path: Path) -> None:
        target = tmp_path / "token.json"
        save_token(TokenRecord(api_key="k", access_token="t"), path=target)
        mode = stat.S_IMODE(target.stat().st_mode)
        assert mode == 0o600


class TestSaveTokenAtomicity:
    def test_no_tempfile_left_behind_on_success(self, tmp_path: Path) -> None:
        save_token(TokenRecord(api_key="k", access_token="t"), path=tmp_path / "t.json")
        # Directory should contain ONLY the target file, no .token-*.tmp.
        files = list(tmp_path.iterdir())
        assert files == [tmp_path / "t.json"]


# ----------------------------------------------------------------------
# login_url
# ----------------------------------------------------------------------


class TestLoginUrl:
    def test_returns_kite_url_with_api_key(self) -> None:
        url = login_url("my_api_key")
        assert "kite" in url.lower() and "my_api_key" in url

    def test_empty_api_key_rejected(self) -> None:
        with pytest.raises(ValueError, match="api_key"):
            login_url("")


# ----------------------------------------------------------------------
# generate_session  (never hits the network; kiteconnect mocked)
# ----------------------------------------------------------------------


class TestGenerateSession:
    def test_success_returns_token_record(self) -> None:
        fake_response = {
            "access_token": "abc123",
            "user_id": "AB1234",
            "public_token": "ignored",
        }
        with patch(
            "ml4t.india.kite.auth.KiteConnect.generate_session",
            return_value=fake_response,
        ):
            record = generate_session("k", "s", "req")
        assert record.access_token == "abc123"
        assert record.user_id == "AB1234"
        assert record.api_key == "k"

    def test_empty_api_key_rejected(self) -> None:
        with pytest.raises(ValueError, match="api_key"):
            generate_session("", "s", "req")

    def test_empty_api_secret_rejected(self) -> None:
        with pytest.raises(ValueError, match="api_secret"):
            generate_session("k", "", "req")

    def test_empty_request_token_rejected(self) -> None:
        with pytest.raises(ValueError, match="request_token"):
            generate_session("k", "s", "")

    def test_token_exception_translated(self) -> None:
        """TokenException -> TokenExpiredError (preserves India taxonomy)."""
        with patch(
            "ml4t.india.kite.auth.KiteConnect.generate_session",
            side_effect=kexc.TokenException("bad request_token"),
        ):
            with pytest.raises(TokenExpiredError):
                generate_session("k", "s", "req")

    def test_input_exception_translated(self) -> None:
        with patch(
            "ml4t.india.kite.auth.KiteConnect.generate_session",
            side_effect=kexc.InputException("bad checksum"),
        ):
            with pytest.raises(InvalidInputError):
                generate_session("k", "s", "req")

    def test_network_exception_translated(self) -> None:
        with patch(
            "ml4t.india.kite.auth.KiteConnect.generate_session",
            side_effect=kexc.NetworkException("timeout"),
        ):
            with pytest.raises(NetworkError):
                generate_session("k", "s", "req")

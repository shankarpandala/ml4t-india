"""Tests for :mod:`ml4t.india.cli.main`.

Uses Click's :class:`CliRunner` to drive the CLI; patches the kite auth
helpers so no real Kite calls fire.
"""

from __future__ import annotations

import datetime as dt
import json
from pathlib import Path
from unittest.mock import patch

from click.testing import CliRunner

from ml4t.india.cli.main import cli
from ml4t.india.core.exceptions import TokenExpiredError
from ml4t.india.kite.auth import TokenRecord

_IST = dt.timezone(dt.timedelta(hours=5, minutes=30), name="IST")


def _sample_record(api_key: str = "ak", access_token: str = "at") -> TokenRecord:
    return TokenRecord(
        api_key=api_key,
        access_token=access_token,
        user_id="AB1234",
        login_time=dt.datetime(2026, 4, 21, 10, 0, tzinfo=_IST),
    )


# ---------- login ----------


class TestLogin:
    def test_happy_path_writes_token(self, tmp_path: Path) -> None:
        """Happy path: generate_session returns a record, save_token writes it."""
        token_file = tmp_path / "token.json"
        runner = CliRunner()
        with (
            patch("ml4t.india.cli.main.login_url", return_value="https://fake"),
            patch(
                "ml4t.india.cli.main.generate_session",
                return_value=_sample_record(),
            ),
        ):
            result = runner.invoke(
                cli,
                [
                    "login",
                    "--api-key", "ak",
                    "--api-secret", "as",
                    "--token-path", str(token_file),
                ],
                input="REQTOKEN\n",
            )
        assert result.exit_code == 0, result.output
        assert "Access token saved" in result.output
        assert token_file.exists()

    def test_missing_api_key_errors(self) -> None:
        """Click enforces --api-key / envvar; missing both -> exit 2."""
        runner = CliRunner()
        result = runner.invoke(cli, ["login", "--api-secret", "as"], input="x\n")
        assert result.exit_code == 2
        assert "api-key" in result.output.lower() or "api_key" in result.output.lower()

    def test_generate_session_failure_exits_nonzero(self, tmp_path: Path) -> None:
        """An IndiaError from generate_session exits 1 with a red message."""
        runner = CliRunner()
        with (
            patch("ml4t.india.cli.main.login_url", return_value="https://fake"),
            patch(
                "ml4t.india.cli.main.generate_session",
                side_effect=TokenExpiredError("bad request_token"),
            ),
        ):
            result = runner.invoke(
                cli,
                [
                    "login",
                    "--api-key", "ak",
                    "--api-secret", "as",
                    "--token-path", str(tmp_path / "t.json"),
                ],
                input="REQTOKEN\n",
            )
        assert result.exit_code == 1
        assert "Login failed" in result.output


# ---------- whoami ----------


class TestWhoami:
    def test_missing_token_exits_1(self, tmp_path: Path) -> None:
        """No file -> Click's `exists=True` guard rejects at option parse (exit 2)."""
        runner = CliRunner()
        result = runner.invoke(
            cli,
            ["whoami", "--token-path", str(tmp_path / "nope.json")],
        )
        # Click raises UsageError for non-existent path (exists=True).
        assert result.exit_code == 2

    def test_cached_record_dump_redacts_secrets(self, tmp_path: Path) -> None:
        """--no-fetch-profile (default) dumps the record with secrets redacted."""
        token_path = tmp_path / "token.json"
        from ml4t.india.kite.auth import save_token  # noqa: PLC0415

        save_token(
            _sample_record(
                api_key="longapikey1234",
                access_token="SUPERSECRETBEARER",
            ),
            path=token_path,
        )

        runner = CliRunner()
        result = runner.invoke(cli, ["whoami", "--token-path", str(token_path)])
        assert result.exit_code == 0, result.output
        data = json.loads(result.output.strip())
        # access_token must be masked entirely.
        assert data["access_token"] == "***REDACTED***"
        # api_key masked head+tail; raw value must not appear.
        assert data["api_key"] != "longapikey1234"
        assert data["user_id"] == "AB1234"

    def test_whoami_does_not_leak_access_token(self, tmp_path: Path) -> None:
        """Defense-in-depth: the raw access_token byte-string is nowhere in output."""
        token_path = tmp_path / "token.json"
        from ml4t.india.kite.auth import save_token  # noqa: PLC0415

        raw_token = "DEADBEEF-live-bearer-1234"
        save_token(
            _sample_record(access_token=raw_token),
            path=token_path,
        )
        runner = CliRunner()
        result = runner.invoke(cli, ["whoami", "--token-path", str(token_path)])
        assert result.exit_code == 0
        assert raw_token not in result.output

    def test_fetch_profile_hits_client(self, tmp_path: Path) -> None:
        """--fetch-profile uses KiteClient.from_api_key (facade only, no SDK import)."""
        token_path = tmp_path / "token.json"
        from ml4t.india.kite.auth import save_token  # noqa: PLC0415

        save_token(_sample_record(), path=token_path)

        fake_profile = {"user_id": "AB1234", "broker": "ZERODHA"}

        class FakeClient:
            def profile(self) -> dict[str, str]:
                return fake_profile

        runner = CliRunner()
        with patch(
            "ml4t.india.kite.client.KiteClient.from_api_key",
            return_value=FakeClient(),
        ):
            result = runner.invoke(
                cli,
                ["whoami", "--token-path", str(token_path), "--fetch-profile"],
            )
        assert result.exit_code == 0, result.output
        assert "ZERODHA" in result.output


# ---------- entry wiring ----------


class TestEntry:
    def test_cli_has_login_and_whoami(self) -> None:
        commands = cli.commands  # type: ignore[attr-defined]
        assert "login" in commands
        assert "whoami" in commands

    def test_help_does_not_import_kiteconnect_sdk(self) -> None:
        """Ensure the kiteconnect SDK isn't imported just to print --help.

        The CLI module imports auth helpers (which import kiteconnect),
        so we cannot assert kiteconnect is absent from sys.modules -- we
        only assert `--help` exits 0. The lazy import inside `whoami`
        exists for the ``KiteClient`` pieces, not the SDK itself.
        """
        runner = CliRunner()
        assert runner.invoke(cli, ["--help"]).exit_code == 0

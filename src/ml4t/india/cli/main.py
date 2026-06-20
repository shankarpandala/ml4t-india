"""``ml4t-india`` Click CLI.

Phase-1 ships two subcommands:

* ``login``   -- interactive Zerodha Kite auth flow (print login URL,
                 accept ``request_token``, persist ``TokenRecord``).
* ``whoami``  -- verify the cached token by fetching the user profile.
"""

from __future__ import annotations

import dataclasses
import json
import sys
from pathlib import Path

import click

from ml4t.india.core.exceptions import IndiaError
from ml4t.india.kite.auth import (
    default_token_path,
    generate_session,
    load_token,
    login_url,
    save_token,
)


@click.group()
def cli() -> None:
    """Command-line entry point for ml4t-india."""


@cli.command("login")
@click.option(
    "--method",
    type=click.Choice(["manual", "auto"]),
    default="manual",
    show_default=True,
    help=(
        "manual: paste the request_token (default, unchanged). "
        "auto: OPT-IN headless login via password + TOTP read from the OS "
        "keychain (see `store_kite_credentials --auto-setup`)."
    ),
)
@click.option(
    "--api-key",
    envvar="KITE_API_KEY",
    default=None,
    help="Zerodha Kite API key (manual: required; auto: falls back to keychain).",
)
@click.option(
    "--api-secret",
    envvar="KITE_API_SECRET",
    default=None,
    help="Zerodha Kite API secret (manual: required; auto: falls back to keychain).",
    hide_input=True,
)
@click.option(
    "--token-path",
    type=click.Path(dir_okay=False, path_type=Path),
    default=None,
    help=f"Where to persist the token (default: {default_token_path()}).",
)
def login(
    method: str,
    api_key: str | None,
    api_secret: str | None,
    token_path: Path | None,
) -> None:
    """Run the Kite login flow and persist the access token locally.

    The default ``manual`` method prints the Kite login URL, prompts for the
    ``request_token`` from the post-login redirect URL, exchanges it for an
    access token, and writes a :class:`TokenRecord` to disk.

    The OPT-IN ``--method auto`` method reads password + TOTP secret from the
    OS keychain and logs in headlessly. It is strictly weaker security than
    manual login (it co-locates the password and the 2FA seed on one host);
    see the README before enabling it.
    """
    if method == "auto":
        _login_auto(api_key=api_key, api_secret=api_secret, token_path=token_path)
        return

    # Manual flow is unchanged for current users: both are required and a
    # missing value is a click usage error (exit 2), matching the prior
    # `required=True` behavior. They are only optional under --method auto.
    if not api_key:
        raise click.UsageError("Missing option '--api-key' (or set KITE_API_KEY).")
    if not api_secret:
        raise click.UsageError("Missing option '--api-secret' (or set KITE_API_SECRET).")

    url = login_url(api_key)
    click.echo(
        "Open this URL in a browser, log in to Zerodha, then copy the "
        "`request_token` query parameter from the redirected URL:\n"
    )
    click.echo(f"  {url}\n")
    request_token = click.prompt("request_token", type=str).strip()

    try:
        record = generate_session(
            api_key=api_key,
            api_secret=api_secret,
            request_token=request_token,
        )
    except IndiaError as exc:
        click.secho(f"Login failed: {exc}", fg="red", err=True)
        sys.exit(1)

    path = save_token(record, path=token_path)
    click.secho(
        f"Access token saved for user {record.user_id} -> {path}",
        fg="green",
    )


#: OS keychain service and key names shared with
#: ``scripts/store_kite_credentials.py``. The store script writes these;
#: the auto-login path reads them. Nothing here is ever written to disk.
_KEYCHAIN_SERVICE = "ml4t-india"
_AUTO_LOGIN_KEYS = {
    "api_key": "kite_api_key",
    "api_secret": "kite_api_secret",
    "user_id": "kite_user_id",
    "password": "kite_password",
    "totp_secret": "kite_totp_secret",
}

_AUTO_SETUP_HINT = (
    "Install the extra and store credentials first:\n"
    "  pip install 'ml4t-india[auto-login]'\n"
    "  python scripts/store_kite_credentials.py --auto-setup"
)


def _login_auto(
    *,
    api_key: str | None,
    api_secret: str | None,
    token_path: Path | None,
) -> None:
    """OPT-IN headless login: keychain secrets -> automated_login -> save.

    All credentials live in the OS keychain. ``--api-key``/``--api-secret``
    (or their env vars) may override the keychain copies; the password and
    TOTP secret are keychain-only and never accepted on the command line.
    """
    # Lazy-import so non-auto users never need keyring / requests / pyotp.
    try:
        import keyring  # noqa: PLC0415
    except ImportError:
        click.secho(
            "Auto login needs the optional `keyring` dependency.\n" f"{_AUTO_SETUP_HINT}",
            fg="red",
            err=True,
        )
        sys.exit(1)

    def _read(name: str) -> str | None:
        value = keyring.get_password(_KEYCHAIN_SERVICE, _AUTO_LOGIN_KEYS[name])
        return value or None

    api_key = api_key or _read("api_key")
    api_secret = api_secret or _read("api_secret")
    user_id = _read("user_id")
    password = _read("password")
    totp_secret = _read("totp_secret")

    missing = [
        name
        for name, value in (
            ("api_key", api_key),
            ("api_secret", api_secret),
            ("user_id", user_id),
            ("password", password),
            ("totp_secret", totp_secret),
        )
        if not value
    ]
    if missing:
        click.secho(
            "Auto login is missing credentials in the keychain: "
            f"{', '.join(missing)}.\n{_AUTO_SETUP_HINT}",
            fg="red",
            err=True,
        )
        sys.exit(1)
    # All five are present past this point; narrow for the type checker.
    assert api_key and api_secret and user_id and password and totp_secret

    try:
        from ml4t.india.kite.auth import automated_login  # noqa: PLC0415

        record = automated_login(
            api_key=api_key,
            api_secret=api_secret,
            user_id=user_id,
            password=password,
            totp_secret=totp_secret,
        )
    except ImportError:
        click.secho(
            "Auto login needs the optional `pyotp`/`requests` dependencies.\n"
            f"{_AUTO_SETUP_HINT}",
            fg="red",
            err=True,
        )
        sys.exit(1)
    except IndiaError as exc:
        click.secho(f"Login failed: {exc}", fg="red", err=True)
        sys.exit(1)

    path = save_token(record, path=token_path)
    click.secho(
        f"Access token saved for user {record.user_id} -> {path}",
        fg="green",
    )


@cli.command("whoami")
@click.option(
    "--token-path",
    type=click.Path(dir_okay=False, exists=True, path_type=Path),
    default=None,
    help=f"Token file to read (default: {default_token_path()}).",
)
@click.option(
    "--fetch-profile/--no-fetch-profile",
    default=False,
    help="Also fetch the live Kite profile (requires a valid session).",
)
def whoami(token_path: Path | None, fetch_profile: bool) -> None:
    """Print the cached token (and optionally the live Kite profile).

    ``access_token`` is redacted and ``api_key`` is partially masked
    before printing so terminal scrollback / CI logs / screen capture
    tools cannot leak a live bearer token. Use the file directly (via
    ``load_token``) if you need the raw values.
    """
    record = load_token(path=token_path)
    if record is None:
        click.secho("No token on disk. Run `ml4t-india login` first.", fg="red", err=True)
        sys.exit(1)

    # Always dump the cached record with secrets redacted.
    safe = dataclasses.asdict(record)
    safe["access_token"] = "***REDACTED***"
    safe["api_key"] = _mask(record.api_key)
    click.echo(json.dumps(safe, indent=2, default=str))

    if not fetch_profile:
        return

    # Lazy import of the facade so `ml4t-india --help` stays snappy.
    # NOTE: we deliberately do NOT import `kiteconnect` here -- per the
    # AGENTS.md boundary, that SDK must only be imported under
    # `src/ml4t/india/kite/`. `KiteClient.from_api_key` builds the real
    # SDK behind the facade.
    from ml4t.india.kite.client import KiteClient  # noqa: PLC0415

    client = KiteClient.from_api_key(
        api_key=record.api_key,
        access_token=record.access_token,
    )
    try:
        profile = client.profile()
    except IndiaError as exc:
        click.secho(f"Profile fetch failed: {exc}", fg="red", err=True)
        sys.exit(1)
    click.echo(json.dumps(profile, indent=2, default=str))


def _mask(secret: str, keep_head: int = 4, keep_tail: int = 2) -> str:
    """Shorten a secret to a head/tail fingerprint for log-safe printing."""
    if len(secret) <= keep_head + keep_tail:
        return "***"
    return f"{secret[:keep_head]}...{secret[-keep_tail:]}"


if __name__ == "__main__":
    cli()

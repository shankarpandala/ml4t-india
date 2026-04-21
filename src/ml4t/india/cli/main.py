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
    "--api-key",
    envvar="KITE_API_KEY",
    required=True,
    help="Zerodha Kite API key.",
)
@click.option(
    "--api-secret",
    envvar="KITE_API_SECRET",
    required=True,
    help="Zerodha Kite API secret (do NOT commit).",
    hide_input=True,
)
@click.option(
    "--token-path",
    type=click.Path(dir_okay=False, path_type=Path),
    default=None,
    help=f"Where to persist the token (default: {default_token_path()}).",
)
def login(api_key: str, api_secret: str, token_path: Path | None) -> None:
    """Run the Kite login flow and persist the access token locally.

    Prints the Kite login URL, prompts the user to paste back the
    ``request_token`` from the post-login redirect URL, exchanges it
    for an access token, and writes a :class:`TokenRecord` to disk.
    """
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
    """Print the cached token (and optionally the live Kite profile)."""
    record = load_token(path=token_path)
    if record is None:
        click.secho("No token on disk. Run `ml4t-india login` first.", fg="red", err=True)
        sys.exit(1)

    # Always dump the cached record (safe -- no secret is stored).
    click.echo(json.dumps(dataclasses.asdict(record), indent=2, default=str))

    if not fetch_profile:
        return

    # Lazy-import so `ml4t-india --help` doesn't pay the kiteconnect cost.
    from kiteconnect import KiteConnect  # noqa: PLC0415

    from ml4t.india.kite.client import KiteClient  # noqa: PLC0415

    sdk = KiteConnect(api_key=record.api_key)
    client = KiteClient(sdk=sdk, access_token=record.access_token)
    try:
        profile = client.profile()
    except IndiaError as exc:
        click.secho(f"Profile fetch failed: {exc}", fg="red", err=True)
        sys.exit(1)
    click.echo(json.dumps(profile, indent=2, default=str))


if __name__ == "__main__":
    cli()

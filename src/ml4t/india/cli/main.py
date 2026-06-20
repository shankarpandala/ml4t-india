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


@cli.command("agent")
@click.argument("run_dir", type=click.Path(exists=True, path_type=Path))
@click.option(
    "--universe",
    default="indices",
    help="NSE index universe preset threaded as research metadata.",
)
@click.option(
    "--llm",
    type=click.Choice(["mock", "anthropic"]),
    default="mock",
    help="LLM backend: 'mock' (keyless, default) or 'anthropic' (reads "
    "ANTHROPIC_API_KEY).",
)
def agent(run_dir: Path, universe: str, llm: str) -> None:
    """Run the ml4t-agent research-review loop over a run directory.

    ``RUN_DIR`` must contain an ``evidence_pack.json`` (and any rerun /
    delta-review fixtures the selected experiment template needs). The
    default ``--llm mock`` is keyless and offline, so ``--help`` and basic
    runs need no API key or extra LLM SDK.
    """
    from ml4t.india.workflows.agent import IndiaResearchAgent  # noqa: PLC0415

    llm_client = None
    if llm == "anthropic":
        # Lazy-build the Anthropic adapter so the default mock path never
        # imports the SDK or requires a key. AnthropicClient reads
        # ANTHROPIC_API_KEY from the environment itself.
        try:
            from ml4t.agent.llm.anthropic import AnthropicClient  # noqa: PLC0415
        except ImportError as exc:
            click.secho(
                "anthropic backend needs the LLM extra: "
                "pip install ml4t-india[agent-anthropic]",
                fg="red",
                err=True,
            )
            raise SystemExit(1) from exc
        llm_client = AnthropicClient()

    research_agent = IndiaResearchAgent(llm=llm_client, universe=universe)
    note = research_agent.run(run_dir)
    click.echo(
        json.dumps(
            {
                "line_id": note.line_id,
                "iteration_index": note.iteration_index,
                "decision": note.decision.value,
                "selected_experiment_id": note.selected_experiment_id,
                "n_proposals": len(note.proposals),
                "summary": note.summary,
                "rationale": note.rationale,
            },
            indent=2,
            default=str,
        )
    )


def _mask(secret: str, keep_head: int = 4, keep_tail: int = 2) -> str:
    """Shorten a secret to a head/tail fingerprint for log-safe printing."""
    if len(secret) <= keep_head + keep_tail:
        return "***"
    return f"{secret[:keep_head]}...{secret[-keep_tail:]}"


if __name__ == "__main__":
    cli()

"""``blizzard hub login``/``logout``/``rotate-signing-key`` — bare verbs over the hub's own ``/api/auth`` surface."""

from __future__ import annotations

import contextlib

import click

from blizzard.hub import session_store
from blizzard.hub.cli import login as cli_login
from blizzard.hub.cli.command import AuthCommand
from blizzard.hub.cli.context import CliContext


@click.command("rotate-signing-key", cls=AuthCommand)
def rotate_signing_key(cli: CliContext) -> None:
    """Rotate the hub's IdP signing keypair (issue #95) — mints a fresh current key,
    demoting the old current to previous; no restart. A no-op error under ``auth.mode = "none"``
    (no keypair exists). Human-plane, gated on ``user:manage`` — under ``auth.mode =
    "oauth"`` this requires a hub session (``blizzard hub login``, issue #96)."""
    cli.post(
        "/api/auth/rotate-signing-key",
        "POST /auth/rotate-signing-key",
        on_status={404: "the IdP surface is not enabled (auth.mode=none)"},
    )
    click.echo("signing key rotated")


@click.command(cls=AuthCommand)
@click.option(
    "--paste",
    "paste",
    is_flag=True,
    default=False,
    help="Use the paste-code flow (no local loopback listener) instead of opening a browser.",
)
@click.option(
    "--no-browser", "no_browser", is_flag=True, default=False, help="Print the login URL instead of opening it."
)
def login(cli: CliContext, paste: bool, no_browser: bool) -> None:
    """Log into the hub (issue #96) — opens the browser to the hub's own authorize
    endpoint (PKCE, an ephemeral ``127.0.0.1`` loopback redirect) and stores the
    resulting session token locally. The CLI never contacts a provider directly.
    ``--paste`` uses the paste-code fallback for a shell with no reachable loopback
    listener; ``--no-browser`` still runs the loopback flow, printing the URL."""
    try:
        flow = (
            cli_login.Login.paste_code(cli.hub_url, prompt_for_code=lambda: click.prompt("Paste the code"))
            if paste
            else cli_login.Login.loopback(cli.hub_url, open_browser=not no_browser)
        )
        token = flow.token()
    except cli_login.LoginError as exc:
        raise click.ClickException(f"login failed: {exc}") from exc
    session_store.SessionFile.of().save(cli.hub_url, token)
    click.echo(f"logged in to {cli.hub_url}")


@click.command(cls=AuthCommand)
def logout(cli: CliContext) -> None:
    """Log out of the hub (issue #96) — deletes the locally stored session token and
    revokes it at the hub, so it stops resolving even if it leaked. A no-op (locally)
    if never logged in; the revoke call is best-effort (a hub already unreachable, or
    an already-expired session, does not block the local cleanup)."""
    with contextlib.suppress(click.ClickException):
        cli.send("post", "/api/auth/logout")
    session_store.SessionFile.of().delete(cli.hub_url)
    click.echo(f"logged out of {cli.hub_url}")

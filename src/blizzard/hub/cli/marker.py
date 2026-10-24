"""``blizzard hub record-marker`` — record a marker artifact mid-run."""

from __future__ import annotations

import os

import click

from blizzard.cli.collaborators import CliCollaborators, HTTPError
from blizzard.hub.api.marker_auth import _MARKER_TOKEN_HEADER
from blizzard.hub.cli.context import CLIENT_TIMEOUT
from blizzard.hub.delivery.hub_node import ENV_MARKER_CALLBACK_URL, ENV_MARKER_TOKEN


@click.command("record-marker")
@click.argument("name")
@click.argument("content", required=False, default="")
@click.pass_context
def record_marker(ctx: click.Context, name: str, content: str) -> None:
    """Deprecated: a hub node's ``run:`` script recorded a marker artifact mid-run; a hub node's action now does.

    The injected ``BZ_HUB_MARKER_CALLBACK_URL`` already carries this run's chunk, node,
    and epoch. Idempotent per marker NAME; authorized by ``BZ_HUB_MARKER_TOKEN``,
    whose absence is named rather than posted unauthenticated."""
    callback_url = os.environ.get(ENV_MARKER_CALLBACK_URL)
    if not callback_url:
        raise click.ClickException(f"record-marker: no {ENV_MARKER_CALLBACK_URL} in the environment")
    marker_token = os.environ.get(ENV_MARKER_TOKEN)
    if not marker_token:
        raise click.ClickException(f"record-marker: no {ENV_MARKER_TOKEN} in the environment")
    try:
        with CliCollaborators.of(ctx).client_factory(
            timeout=CLIENT_TIMEOUT, headers={_MARKER_TOKEN_HEADER: marker_token}
        ) as client:
            client.post(callback_url, json={"name": name, "content": content}).raise_for_status()
    except HTTPError as exc:
        raise click.ClickException(f"record-marker: could not record the marker ({exc})") from exc
    click.echo(f"recorded marker `{name}`")

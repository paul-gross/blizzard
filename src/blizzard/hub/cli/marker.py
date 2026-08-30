"""``blizzard hub record-marker`` — issue #65: record a marker artifact mid-run."""

from __future__ import annotations

import os

import click
import httpx

from blizzard.hub.api.marker_auth import _MARKER_TOKEN_HEADER
from blizzard.hub.cli.context import CLIENT_TIMEOUT
from blizzard.hub.delivery.hub_node import ENV_MARKER_CALLBACK_URL, ENV_MARKER_TOKEN


@click.command("record-marker")
@click.argument("name")
@click.argument("content", required=False, default="")
def record_marker(name: str, content: str) -> None:
    """A hub command node's ``run:`` script: record a marker artifact mid-run (#65).

    The injected ``BZ_HUB_MARKER_CALLBACK_URL`` already carries this run's chunk, node,
    and epoch. Idempotent per marker NAME; authorized by ``BZ_HUB_MARKER_TOKEN``
    (issue #230), whose absence is named rather than posted unauthenticated."""
    callback_url = os.environ.get(ENV_MARKER_CALLBACK_URL)
    if not callback_url:
        raise click.ClickException(f"record-marker: no {ENV_MARKER_CALLBACK_URL} in the environment")
    marker_token = os.environ.get(ENV_MARKER_TOKEN)
    if not marker_token:
        raise click.ClickException(f"record-marker: no {ENV_MARKER_TOKEN} in the environment")
    try:
        resp = httpx.post(
            callback_url,
            json={"name": name, "content": content},
            headers={_MARKER_TOKEN_HEADER: marker_token},
            timeout=CLIENT_TIMEOUT,
        )
        resp.raise_for_status()
    except httpx.HTTPError as exc:
        raise click.ClickException(f"record-marker: could not record the marker ({exc})") from exc
    click.echo(f"recorded marker `{name}`")

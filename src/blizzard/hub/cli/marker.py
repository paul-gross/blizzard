"""``blizzard hub record-marker`` — deprecated, and refuses: a hub step no longer runs a command to call it."""

from __future__ import annotations

import click


@click.command("record-marker")
@click.argument("name")
@click.argument("content", required=False, default="")
def record_marker(name: str, content: str) -> None:
    """Deprecated, and refuses: a hub step no longer runs commands, so no marker can be recorded this way. A hub
    node's action records its own markers. Removed in 0.4."""
    raise click.ClickException(
        "record-marker: a hub step no longer runs commands, so no marker can be recorded this way; "
        "a hub node's action records its own markers. This command is removed in 0.4."
    )

"""``blizzard hub settings`` — the tenant's settings: one record, keyed by the tenant, shown and edited."""

from __future__ import annotations

from typing import Any

import click

from blizzard.cli.get_group import GetGroup
from blizzard.hub.cli.command import FleetCommand
from blizzard.hub.cli.context import CliContext


@click.group("settings", cls=GetGroup)
def settings_group() -> None:
    """Show or edit the tenant's settings."""


def _show(cli: CliContext, body: Any) -> None:
    cli.show_lines(
        body,
        f"default urgency {body['default_urgency']['slug']}",
        f"revision {body['revision']}",
        f"updated {body['updated_at']} by {body['updated_by']}",
    )


@settings_group.command("get", cls=FleetCommand)
def settings_get(cli: CliContext) -> None:
    """Show the tenant's settings — its default urgency, what every new chunk takes when nothing names another."""
    _show(cli, cli.get("/api/settings", "GET /settings").json())


@settings_group.command("edit", cls=FleetCommand)
@click.option("--default-urgency", required=True, metavar="SLUG", help="The live urgency new chunks take.")
def settings_edit(cli: CliContext, default_urgency: str) -> None:
    """Change the tenant's default urgency. No chunk moves; it decides only what is minted from then on."""
    resp = cli.patch("/api/settings", "PATCH /settings", json_body={"default_urgency": default_urgency}, door=True)
    _show(cli, resp.json())

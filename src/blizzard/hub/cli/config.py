"""``blizzard hub config`` — operator verbs over the configuration change log."""

from __future__ import annotations

from typing import Any

import click

from blizzard.hub.cli.command import FleetCommand
from blizzard.hub.cli.context import CliContext
from blizzard.hub.cli.views import Listing

_KINDS = click.Choice(["work_source", "repository", "secret"])


class ChangeListing(Listing):
    empty = "no configuration changes yet"

    def line(self, row: Any) -> str:
        fields = ",".join(d["field"] for d in row["diff"])
        tail = f"  {fields}" if fields else ""
        return (
            f"#{row['id']}  {row['recorded_at']}  {row['actor']}  {row['door']}  "
            f"{row['record_kind']} {row['record_key']}  r{row['revision']}  {row['op']}{tail}"
        )


@click.group("config")
def config_group() -> None:
    """Operator verbs over configuration: changes."""


@config_group.command("changes", cls=FleetCommand)
@click.option("--kind", "kind", type=_KINDS, default=None, help="Only changes to this kind of record.")
@click.option("--key", "key", default=None, help="Only changes to the record with this name.")
@click.option(
    "--all", "everything", is_flag=True, default=False, help="Print every change rather than the newest page."
)
def config_changes(cli: CliContext, kind: str | None, key: str | None, everything: bool) -> None:
    """List configuration changes newest first — who changed which record, through which door, and what.

    Shows the newest page unless --all. A secret's value never appears."""
    params = {k: v for k, v in (("record_kind", kind), ("record_key", key)) if v is not None}
    rows: list[Any] = []
    while True:
        body = cli.get("/api/config/changes", "GET /config/changes", params=params).json()
        rows.extend(body["changes"])
        if not everything or body["next_before"] is None:
            break
        params["before"] = str(body["next_before"])
    cli.show({"changes": rows}, ChangeListing(rows))

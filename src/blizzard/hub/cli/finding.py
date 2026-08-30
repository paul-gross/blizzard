"""``blizzard hub finding`` — blizzard#390: read verbs over findings."""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
from typing import Any

import click

from blizzard.hub.cli.command import FleetCommand
from blizzard.hub.cli.context import CliContext
from blizzard.hub.cli.views import Listing


class FindingListing(Listing):
    empty = "no findings"

    def line(self, row: Any) -> str:
        marker = "live" if row["live"] else "gone"
        return f"{row['finding_id']}  {marker}  class={row['class']}  {row['locus']}"


@dataclass(frozen=True)
class FindingDetail:
    body: dict[str, Any]

    def lines(self) -> Iterator[str]:
        body = self.body
        marker = "live" if body["live"] else "gone"
        yield f"{body['finding_id']}  {marker}  routine={body['routine_name']}  scope={body['scope_slug']}"
        yield f"  class={body['class']}  locus={body['locus']}"
        yield f"  {body['summary']}"
        if body.get("introduced"):
            yield f"  introduced: {body['introduced']}"
        yield f"  last_seen_at={body.get('last_seen_at') or '-'}  observed_count={body['observed_count']}"


@click.group("finding")
def finding_group() -> None:
    """Read verbs over findings: a routine's bucket under one scope, or one by id."""


@finding_group.command("list", cls=FleetCommand)
@click.option("--routine", "routine", required=True, help="The routine whose findings to list.")
@click.option("--scope", "scope", required=True, help="The scope to filter to.")
@click.option("--include-gone", is_flag=True, default=False, help="Also show findings whose newest fact is gone (D3).")
def finding_list(cli: CliContext, routine: str, scope: str, include_gone: bool) -> None:
    """List ROUTINE's findings under SCOPE — live only, unless --include-gone.

    This is the read a running pass calls to cross-reference its own bucket
    (blizzard-context:/domain/findings-and-proposals.md)."""
    rows = cli.get(
        "/api/findings",
        "GET /findings",
        params={"routine": routine, "scope": scope, "include_gone": str(include_gone).lower()},
    ).json()
    cli.show(rows, FindingListing(rows))


@finding_group.command("show", cls=FleetCommand)
@click.argument("finding_id")
def finding_show(cli: CliContext, finding_id: str) -> None:
    """One finding's whole record."""
    resp = cli.get(
        f"/api/findings/{finding_id}", "GET /findings/{id}", on_status={404: f"unknown finding {finding_id}"}
    )
    body = resp.json()
    cli.show(body, FindingDetail(body))

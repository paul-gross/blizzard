"""``blizzard hub garden-proposal`` — blizzard#390: read verbs over garden proposals."""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
from typing import Any

import click

from blizzard.hub.cli.command import FleetCommand
from blizzard.hub.cli.context import CliContext
from blizzard.hub.cli.views import Listing


class GardenProposalListing(Listing):
    empty = "no garden proposals"

    def line(self, row: Any) -> str:
        return f"{row['proposal_id']}  class={row['class']}  {row['title']}"


@dataclass(frozen=True)
class GardenProposalDetail:
    body: dict[str, Any]

    def lines(self) -> Iterator[str]:
        body = self.body
        yield f"{body['proposal_id']}  routine={body['routine_name']}  class={body['class']}"
        yield f"  {body['title']}"
        yield f"  {body['body']}"
        yield f"  findings: {', '.join(body['findings'])}"


@click.group("garden-proposal")
def garden_proposal_group() -> None:
    """Read verbs over garden proposals: list every one, or inspect one by id."""


@garden_proposal_group.command("list", cls=FleetCommand)
def garden_proposal_list(cli: CliContext) -> None:
    """List every garden proposal, newest first."""
    rows = cli.get("/api/garden-proposals", "GET /garden-proposals").json()
    cli.show(rows, GardenProposalListing(rows))


@garden_proposal_group.command("show", cls=FleetCommand)
@click.argument("proposal_id")
def garden_proposal_show(cli: CliContext, proposal_id: str) -> None:
    """One garden proposal's whole record."""
    resp = cli.get(
        f"/api/garden-proposals/{proposal_id}",
        "GET /garden-proposals/{id}",
        on_status={404: f"unknown garden proposal {proposal_id}"},
    )
    body = resp.json()
    cli.show(body, GardenProposalDetail(body))

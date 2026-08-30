"""``blizzard hub decision`` — issue #104: operator verbs over open gate decisions (list, resolve)."""

from __future__ import annotations

from typing import Any

import click
import httpx

from blizzard.hub.cli.command import FleetCommand
from blizzard.hub.cli.context import CliContext
from blizzard.hub.cli.views import Listing


class DecisionListing(Listing):
    empty = "no open decisions"

    def line(self, row: Any) -> str:
        choices = ", ".join(c["name"] for c in row.get("choices", []))
        return f"{row['decision_id']}  chunk={row['chunk_id']}  node={row['node_name']}  choices=[{choices}]"


@click.group("decision")
def decision_group() -> None:
    """Operator verbs over open gate decisions: list, resolve."""


@decision_group.command("list", cls=FleetCommand)
def decision_list(cli: CliContext) -> None:
    """List open decisions awaiting a human (gate surfacing)."""
    body = cli.get("/api/decisions", "GET /decisions").json()
    cli.show(body, DecisionListing(body.get("decisions", [])))


@decision_group.command("resolve", cls=FleetCommand)
@click.argument("decision_id")
@click.argument("choice")
@click.option("--by", "resolved_by", default="operator", help="Who is resolving (recorded on the resolution).")
def decision_resolve(cli: CliContext, decision_id: str, choice: str, resolved_by: str) -> None:
    """Resolve an open decision by picking CHOICE (first-write-wins).

    A pure client of ``POST /api/decisions/{id}/resolutions`` (issue #104's pluralized
    resolution route)."""
    resp = cli.send(
        "post", f"/api/decisions/{decision_id}/resolutions", json_body={"choice": choice, "resolved_by": resolved_by}
    )
    if resp.status_code == httpx.codes.CONFLICT:
        winner = resp.json()
        raise click.ClickException(f"already resolved by {winner.get('already_resolved_by')}")
    cli.check(
        resp,
        "POST /decisions/{id}/resolutions",
        on_status={404: f"no such decision {decision_id}", 400: "invalid choice", 422: "invalid choice"},
    )
    body = resp.json()
    cli.show_lines(body, f"decision {decision_id} resolved: {body['choice']} (by {body['resolved_by']})")

"""``blizzard hub decision`` — operator commands over open gate decisions."""

from __future__ import annotations

from http import HTTPStatus
from typing import Any

import click

from blizzard.hub.cli.command import FleetCommand
from blizzard.hub.cli.context import CliContext
from blizzard.hub.cli.project_option import project_option, project_query
from blizzard.hub.cli.views import Listing


class DecisionListing(Listing):
    empty = "no open decisions"

    def line(self, row: Any) -> str:
        choices = ", ".join(c["name"] for c in row.get("choices", []))
        checks = row.get("check_results", [])
        red = sum(1 for c in checks if not c["passed"])
        summary = f"  checks={red}/{len(checks)} red" if checks else ""
        return f"{row['decision_id']}  chunk={row['chunk_id']}  node={row['node_name']}  choices=[{choices}]{summary}"


@click.group("decision")
def decision_group() -> None:
    """Read and resolve the decisions a gate waits on a human for."""


@decision_group.command("list", cls=FleetCommand)
@project_option(required_by_hub=False)
def decision_list(cli: CliContext, project: str | None) -> None:
    """List open decisions awaiting a human (gate surfacing), or only one project's."""
    body = cli.get("/api/decisions", "GET /decisions", params=project_query(project)).json()
    cli.show(body, DecisionListing(body.get("decisions", [])))


@decision_group.command("resolve", cls=FleetCommand)
@click.argument("decision_id")
@click.argument("choice")
@click.option("--by", "resolved_by", default="operator", help="Who is resolving, recorded as the actor.")
@click.option("--note", default=None, help="A note binding every later session on the chunk, as an answer does.")
def decision_resolve(cli: CliContext, decision_id: str, choice: str, resolved_by: str, note: str | None) -> None:
    """Resolve an open decision by picking CHOICE; the chunk then moves on along that choice.

    Only the first resolution counts: a later one is refused and told who already resolved.
    Refused too when CHOICE is not one of the decision's choices, or the decision was closed
    undecided by a restart or by its chunk ending."""
    request = {"choice": choice, "resolved_by": resolved_by}
    if note is not None:
        request["note"] = note
    resp = cli.send("post", f"/api/decisions/{decision_id}/resolutions", json_body=request)
    if resp.status_code == HTTPStatus.CONFLICT:
        body = resp.json()
        if "already_resolved_by" in body:
            raise click.ClickException(f"already resolved by {body['already_resolved_by']}")
        raise click.ClickException(str(body.get("detail", "decision is closed")))
    cli.check(
        resp,
        "POST /decisions/{id}/resolutions",
        on_status={404: f"no such decision {decision_id}", 400: "invalid choice", 422: "invalid choice"},
    )
    body = resp.json()
    override = " — overrode red checks" if body.get("overrode_red_checks") else ""
    cli.show_lines(body, f"decision {decision_id} resolved: {body['choice']} (by {body['resolved_by']}){override}")

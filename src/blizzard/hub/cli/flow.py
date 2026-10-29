"""``blizzard hub flow`` — each branch's delivery line: the chunks waiting to land on it, and its record."""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
from typing import Any, cast

import click

from blizzard.foundation.roles import dto
from blizzard.hub.cli.command import FleetCommand
from blizzard.hub.cli.context import CliContext
from blizzard.hub.cli.views import Listing

#: The most entries one record read asks the hub for.
_RECORD_PAGE = 200


def flow_label(row: dict[str, Any]) -> str:
    """A flow's branch as an operator names it: its repository's slug, else its resource, then the branch."""
    return f"{row.get('repository_slug') or row['resource']}@{row['branch']}"


def place_label(place: dict[str, Any]) -> str:
    """A place as the hub judged it: its state, its position, and when a ``polling`` place next polls."""
    poll = f"  next poll {place['next_poll_at']}" if place.get("next_poll_at") else ""
    return f"{place['state']}  position {place['position']}{poll}"


def _fact_line(fact: dict[str, Any]) -> str:
    detail = "".join(f"  {key}={fact[key]}" for key in ("outcome", "revision") if fact.get(key))
    return f"{fact['id']}  {fact['at']}  {fact['kind']}  {fact['chunk_id']}{detail}"


@dto
@dataclass(frozen=True)
class FlowDetail:
    body: dict[str, Any]

    def lines(self) -> Iterator[str]:
        body = self.body
        turn = body.get("turn")
        yield f"{body['flow_id']}  {flow_label(body)}  turn={turn['chunk_id'] if turn else '-'}"
        for place in cast(list[dict[str, Any]], body.get("line") or []):
            yield f"  {place['chunk_id']}  {place_label(place)}"
        for title, key in (("last verdict", "last_verdict"), ("last landing", "last_landing")):
            fact = body.get(key)
            if fact:
                yield f"  {title}: {_fact_line(fact)}"


@dto
@dataclass(frozen=True)
class FlowListing:
    flows: list[dict[str, Any]]

    def lines(self) -> Iterator[str]:
        if not self.flows:
            yield "no flows"
        for flow in self.flows:
            yield from FlowDetail(flow).lines()


class FlowRecordListing(Listing):
    empty = "no record"

    def line(self, row: Any) -> str:
        return _fact_line(row)


@click.group("flow")
def flow_group() -> None:
    """Inspect each branch's delivery line and its record."""


@flow_group.command("list", cls=FleetCommand)
@click.option(
    "--all",
    "everything",
    is_flag=True,
    default=False,
    help="Every flow, not only those with a chunk in line or a change in the last 48 hours.",
)
def flow_list(cli: CliContext, everything: bool) -> None:
    """List the tenant's flows: each branch's open turn, the chunks in line with their place, and its last verdict
    and landing.

    Shows only flows with a chunk in line or a change in the last 48 hours unless --all."""
    params = {} if everything else {"active": "true"}
    body = cli.get("/api/flows", "GET /flows", params=params).json()
    cli.show(body, FlowListing(body["flows"]))


@flow_group.command("show", cls=FleetCommand)
@click.argument("flow_id")
@click.option("--record", "record", is_flag=True, default=False, help="Print the flow's record, newest first, instead.")
@click.option(
    "--limit",
    "limit",
    type=click.IntRange(min=1),
    default=50,
    show_default=True,
    help="With --record, the most entries to print.",
)
def flow_show(cli: CliContext, flow_id: str, record: bool, limit: int) -> None:
    """Show one flow: its open turn, the chunks in line with their place, and its last verdict and landing.

    With --record, the flow's record instead — every submission, turn, landing and withdrawal, newest first, up to
    --limit entries."""
    on_status = {404: f"unknown flow {flow_id}"}
    if not record:
        body = cli.get(f"/api/flows/{flow_id}", "GET /flows/{id}", on_status=on_status).json()
        cli.show(body, FlowDetail(body))
        return
    facts: list[Any] = []
    params: dict[str, str] = {}
    while True:
        params["limit"] = str(min(limit - len(facts), _RECORD_PAGE))
        page = cli.get(f"/api/flows/{flow_id}/record", "GET /flows/{id}/record", params=params, on_status=on_status)
        body = page.json()
        facts.extend(body["facts"])
        if len(facts) >= limit or body["next_before"] is None:
            break
        params["before"] = str(body["next_before"])
    cli.show({"facts": facts, "next_before": body["next_before"]}, FlowRecordListing(facts))

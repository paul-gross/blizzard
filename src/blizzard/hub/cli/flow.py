"""``blizzard hub flow`` — each branch's delivery line: the chunks waiting to land on it, and its record."""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
from typing import Any, cast

import click
from pydantic import BaseModel, TypeAdapter, model_validator

from blizzard.cli.get_group import GetGroup
from blizzard.foundation.roles import dto
from blizzard.hub.cli.command import FleetCommand
from blizzard.hub.cli.context import CliContext
from blizzard.hub.cli.views import Listing
from blizzard.wire.flow import ChunkFlowPlaceView, FlowFactView, FlowListView, FlowPlaceView, FlowRecordPage, FlowView

#: The most entries one record read asks the hub for.
_RECORD_PAGE = 200


class RecordPageAnswer(BaseModel):
    """A flow-record page as the hub answered it: decoded against :class:`FlowRecordPage`, its facts also kept as
    sent so ``--json`` prints them unchanged."""

    raw_facts: list[Any]
    page: FlowRecordPage

    @model_validator(mode="before")
    @classmethod
    def _of_answer(cls, value: Any) -> Any:
        if isinstance(value, dict):
            answer = cast(dict[str, Any], value)
            return {"raw_facts": answer.get("facts", []), "page": answer}
        return value

    @property
    def next_before(self) -> int | None:
        return self.page.next_before

    def rows(self) -> list[tuple[Any, FlowFactView]]:
        """Each fact as sent beside its decoded form."""
        return list(zip(self.raw_facts, self.page.facts, strict=True))


_FLOWS = TypeAdapter(FlowListView)
_FLOW = TypeAdapter(FlowView)
_RECORD = TypeAdapter(RecordPageAnswer)


def flow_label(row: FlowView | ChunkFlowPlaceView) -> str:
    """A flow's branch as an operator names it: its repository's slug, else its resource, then the branch."""
    return f"{row.repository_slug or row.resource}@{row.branch}"


def place_label(place: FlowPlaceView | ChunkFlowPlaceView) -> str:
    """A place as the hub judged it: its state, its position, and when a ``polling`` place next polls."""
    poll = f"  next poll {place.next_poll_at}" if place.next_poll_at else ""
    return f"{place.state}  position {place.position}{poll}"


def _fact_line(fact: FlowFactView) -> str:
    detail = "".join(
        f"  {key}={value}" for key, value in (("outcome", fact.outcome), ("revision", fact.revision)) if value
    )
    return f"{fact.id}  {fact.at}  {fact.kind}  {fact.chunk_id}{detail}"


@dto
@dataclass(frozen=True)
class FlowDetail:
    body: FlowView

    def lines(self) -> Iterator[str]:
        body = self.body
        turn = body.turn
        yield f"{body.flow_id}  {flow_label(body)}  turn={turn.chunk_id if turn else '-'}"
        for place in body.line:
            yield f"  {place.chunk_id}  {place_label(place)}"
        for title, fact in (("last verdict", body.last_verdict), ("last landing", body.last_landing)):
            if fact:
                yield f"  {title}: {_fact_line(fact)}"


@dto
@dataclass(frozen=True)
class FlowListing:
    flows: list[FlowView]

    def lines(self) -> Iterator[str]:
        if not self.flows:
            yield "no flows"
        for flow in self.flows:
            yield from FlowDetail(flow).lines()


class FlowRecordListing(Listing):
    empty = "no record"

    def line(self, row: Any) -> str:
        return _fact_line(row)


@click.group("flow", cls=GetGroup)
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
    raw, body = cli.decode(cli.get("/api/flows", "GET /flows", params=params), _FLOWS, "GET /flows")
    cli.show(raw, FlowListing(body.flows))


@flow_group.command("get", cls=FleetCommand)
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
    if not record:
        raw, body = cli.decode(cli.get(f"/api/flows/{flow_id}", "GET /flows/{id}"), _FLOW, "GET /flows/{id}")
        cli.show(raw, FlowDetail(body))
        return
    facts, next_before = cli.get_before_pages(
        f"/api/flows/{flow_id}/record",
        "GET /flows/{id}/record",
        page=_RECORD,
        rows_of=RecordPageAnswer.rows,
        params={"limit": str(_RECORD_PAGE)},
        want=limit,
    )
    cli.show(
        {"facts": [raw for raw, _ in facts], "next_before": next_before},
        FlowRecordListing([fact for _, fact in facts]),
    )

"""``blizzard hub queue`` — operator commands over the ready queue."""

from __future__ import annotations

import click
from pydantic import TypeAdapter

from blizzard.cli.get_group import GetGroup
from blizzard.hub.cli.command import FleetCommand
from blizzard.hub.cli.context import CliContext
from blizzard.hub.cli.project_option import project_option, project_query
from blizzard.hub.cli.views import Listing, urgency_label
from blizzard.wire.queue import QueuePageView, QueuePeekEntry, QueuePeekResponse

_PAGE = TypeAdapter(QueuePageView)
_ORDER = TypeAdapter(QueuePeekResponse)


class QueueListing(Listing):
    empty = "queue is empty"

    def line(self, row: QueuePeekEntry) -> str:
        marking = f"  [blocked on {row.blocked.prerequisite_chunk_id}]" if row.blocked else ""
        urgency = urgency_label(row.urgency)
        return f"{row.position}  {row.chunk_id}  urgency={urgency}  graph={row.graph_id}{marking}"


@click.group("queue", cls=GetGroup)
def queue_group() -> None:
    """Read and reorder the queue of ready chunks runners claim from."""


@queue_group.command("get", cls=FleetCommand)
@project_option(required_by_hub=False)
def queue_show(cli: CliContext, project: str | None) -> None:
    """Show the whole ready queue, front first."""
    pairs = cli.get_all(
        "/api/queue", "GET /queue", page=_PAGE, rows=lambda p: p.entries, key="entries", params=project_query(project)
    )
    cli.show({"entries": [raw for raw, _ in pairs]}, QueueListing([entry for _, entry in pairs]))


@queue_group.command("set", cls=FleetCommand)
@click.argument("chunk_ids", nargs=-1, required=True)
def queue_set(cli: CliContext, chunk_ids: tuple[str, ...]) -> None:
    """Replace the whole ready-queue order with CHUNK_IDS, front to back.

    Ready chunks not named keep their relative order after the named ones. Refused when an
    id is not ready (a backlog chunk included) or repeats."""
    resp = cli.put(
        "/api/queue",
        "PUT /queue",
        json_body={"chunk_ids": list(chunk_ids)},
    )
    raw, order = cli.decode(resp, _ORDER, "PUT /queue")
    cli.show_lines(raw, f"queue order set ({len(order.entries)} ready chunk(s))")


@queue_group.command("move", cls=FleetCommand)
@click.argument("chunk_id")
@click.argument("position", type=int)
def queue_move(cli: CliContext, chunk_id: str, position: int) -> None:
    """Move CHUNK_ID to POSITION in the ready queue (``0`` is the front).

    No other chunk changes place. Refused when CHUNK_ID is not ready, a backlog chunk included."""
    pairs = cli.get_all("/api/queue", "GET /queue", page=_PAGE, rows=lambda p: p.entries, key="entries")
    rest = [entry.chunk_id for _, entry in pairs if entry.chunk_id != chunk_id]
    index = min(max(position, 0), len(rest))
    after_chunk_id = rest[index - 1] if index > 0 else None
    resp = cli.post(
        "/api/queue/position",
        "POST /queue/position",
        json_body={"chunk_id": chunk_id, "after_chunk_id": after_chunk_id},
    )
    _, order = cli.decode(resp, _ORDER, "POST /queue/position")
    landed = next(entry.position for entry in order.entries if entry.chunk_id == chunk_id)
    cli.finish(resp, f"moved {chunk_id} to position {landed}", "POST /queue/position", answer=_ORDER)

"""``blizzard hub queue`` — issues #87/#104: operator verbs over the ready queue."""

from __future__ import annotations

from typing import Any

import click

from blizzard.hub.cli.command import FleetCommand
from blizzard.hub.cli.context import CliContext
from blizzard.hub.cli.views import Listing


class QueueListing(Listing):
    empty = "queue is empty"

    def line(self, row: Any) -> str:
        blocked = row.get("blocked")
        marking = f"  [blocked on {blocked['prerequisite_chunk_id']}]" if blocked else ""
        return f"{row['position']}  {row['chunk_id']}  graph={row.get('graph_id')}{marking}"


@click.group("queue")
def queue_group() -> None:
    """Operator verbs over the ready queue: show its order, replace it, move one chunk."""


@queue_group.command("show", cls=FleetCommand)
def queue_show(cli: CliContext) -> None:
    """The hub-ordered ready queue, read-only — a client of ``GET /api/queue``."""
    body = cli.get("/api/queue", "GET /queue").json()
    cli.show(body, QueueListing(body.get("entries", [])))


@queue_group.command("set", cls=FleetCommand)
@click.argument("chunk_ids", nargs=-1, required=True)
def queue_set(cli: CliContext, chunk_ids: tuple[str, ...]) -> None:
    """Replace the whole ready-queue order with CHUNK_IDS, front to back.

    A pure client of ``PUT /api/queue`` — an idempotent whole-order replacement
    (issue #104). Every id must be in the ready list, not the backlog (409), and must
    not repeat (422); a chunk not named keeps its relative order, appended last."""
    resp = cli.put(
        "/api/queue",
        "PUT /queue",
        json_body={"chunk_ids": list(chunk_ids)},
        on_status={
            409: "one of the named chunks is not in the ready list (it may be in the not_ready backlog instead)",
            422: "chunk_ids must not repeat",
        },
    )
    body = resp.json()
    cli.show_lines(body, f"queue order set ({len(body.get('entries', []))} ready chunk(s))")


@queue_group.command("move", cls=FleetCommand)
@click.argument("chunk_id")
@click.argument("position", type=int)
def queue_move(cli: CliContext, chunk_id: str, position: int) -> None:
    """Move CHUNK_ID to POSITION in the ready queue (``0`` is the front).

    A client of the single-chunk fractional ``POST /api/queue/position`` (issue #137):
    reads the current order, drops CHUNK_ID out of it, clamps POSITION into what's left,
    and sends one anchor. 409 when CHUNK_ID is not in the ready list, not the backlog."""
    peek = cli.get("/api/queue", "GET /queue")
    rest = [entry["chunk_id"] for entry in peek.json().get("entries", []) if entry["chunk_id"] != chunk_id]
    index = min(max(position, 0), len(rest))
    after_chunk_id = rest[index - 1] if index > 0 else None
    resp = cli.post(
        "/api/queue/position",
        "POST /queue/position",
        json_body={"chunk_id": chunk_id, "after_chunk_id": after_chunk_id},
        on_status={409: f"chunk {chunk_id} is not in the ready list (it may be in the not_ready backlog instead)"},
    )
    cli.finish(resp, f"moved {chunk_id} to position {position}")

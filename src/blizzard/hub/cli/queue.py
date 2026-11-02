"""``blizzard hub queue`` — operator commands over the ready queue."""

from __future__ import annotations

from typing import Any, cast

import click

from blizzard.cli.get_group import GetGroup
from blizzard.hub.cli.command import FleetCommand
from blizzard.hub.cli.context import CliContext
from blizzard.hub.cli.project_option import project_option, project_query
from blizzard.hub.cli.views import Listing


class QueueListing(Listing):
    empty = "queue is empty"

    def line(self, row: Any) -> str:
        blocked = row.get("blocked")
        marking = f"  [blocked on {blocked['prerequisite_chunk_id']}]" if blocked else ""
        return f"{row['position']}  {row['chunk_id']}  graph={row.get('graph_id')}{marking}"


@click.group("queue", cls=GetGroup)
def queue_group() -> None:
    """Read and reorder the queue of ready chunks runners claim from."""


@queue_group.command("get", cls=FleetCommand)
@project_option(required_by_hub=False)
def queue_show(cli: CliContext, project: str | None) -> None:
    """Show the whole ready queue, front first."""
    entries = cli.get_all("/api/queue", "GET /queue", key="entries", params=project_query(project))
    cli.show({"entries": entries}, QueueListing(entries))


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

    No other chunk changes place. Refused when CHUNK_ID is not ready, a backlog chunk included."""
    entries = cli.get_all("/api/queue", "GET /queue", key="entries")
    rest = [cast(str, entry["chunk_id"]) for entry in entries if entry["chunk_id"] != chunk_id]
    index = min(max(position, 0), len(rest))
    after_chunk_id = rest[index - 1] if index > 0 else None
    resp = cli.post(
        "/api/queue/position",
        "POST /queue/position",
        json_body={"chunk_id": chunk_id, "after_chunk_id": after_chunk_id},
        on_status={409: f"chunk {chunk_id} is not in the ready list (it may be in the not_ready backlog instead)"},
    )
    landed = next(entry["position"] for entry in resp.json()["entries"] if entry["chunk_id"] == chunk_id)
    cli.finish(resp, f"moved {chunk_id} to position {landed}")

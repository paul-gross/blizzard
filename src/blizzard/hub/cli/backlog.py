"""``blizzard hub backlog`` — operator commands over the not-ready backlog."""

from __future__ import annotations

import click
from pydantic import TypeAdapter

from blizzard.cli.get_group import GetGroup
from blizzard.hub.cli.command import FleetCommand
from blizzard.hub.cli.context import CliContext
from blizzard.hub.cli.pages import read_pages
from blizzard.hub.cli.project_option import project_option, project_query
from blizzard.hub.cli.views import Listing, urgency_label
from blizzard.wire.queue import BacklogPageView, BacklogPeekEntry, BacklogPeekResponse

_PAGE = TypeAdapter(BacklogPageView)
_ORDER = TypeAdapter(BacklogPeekResponse)


class BacklogListing(Listing):
    empty = "backlog is empty"

    def line(self, row: BacklogPeekEntry) -> str:
        marking = f"  [blocked on {row.blocked.prerequisite_chunk_id}]" if row.blocked else ""
        urgency = urgency_label(row.urgency)
        return f"{row.position}  {row.chunk_id}  urgency={urgency}  graph={row.graph_id}{marking}"


@click.group("backlog", cls=GetGroup)
def backlog_group() -> None:
    """Read and reorder the backlog of not-ready chunks.

    Backlog order never makes a chunk runnable: none of these commands promotes or demotes work."""


@backlog_group.command("get", cls=FleetCommand)
@project_option(required_by_hub=False)
def backlog_show(cli: CliContext, project: str | None) -> None:
    """Show the whole backlog, front first, at its whole-list positions."""
    raw, pages = read_pages(
        cli, "/api/backlog", "GET /backlog", page=_PAGE, key="entries", params=project_query(project)
    )
    cli.show({"entries": raw}, BacklogListing([entry for page in pages for entry in page.entries]))


@backlog_group.command("set", cls=FleetCommand)
@click.argument("chunk_ids", nargs=-1, required=True)
def backlog_set(cli: CliContext, chunk_ids: tuple[str, ...]) -> None:
    """Replace the whole backlog order with CHUNK_IDS, front to back.

    Backlog chunks not named keep their relative order after the named ones. Refused when an
    id is not in the backlog (a ready chunk included) or repeats."""
    resp = cli.put(
        "/api/backlog",
        "PUT /backlog",
        json_body={"chunk_ids": list(chunk_ids)},
    )
    raw, order = cli.decode(resp, _ORDER, "PUT /backlog")
    cli.show_lines(raw, f"backlog order set ({len(order.entries)} not-ready chunk(s))")


@backlog_group.command("move", cls=FleetCommand)
@click.argument("chunk_id")
@click.argument("position", type=int)
def backlog_move(cli: CliContext, chunk_id: str, position: int) -> None:
    """Move CHUNK_ID to POSITION in the backlog (``0`` is the front).

    No other chunk changes place. Refused when CHUNK_ID is not in the backlog, a ready chunk included."""
    pages = cli.get_all("/api/backlog", "GET /backlog", page=_PAGE)
    rest = [entry.chunk_id for page in pages for entry in page.entries if entry.chunk_id != chunk_id]
    index = min(max(position, 0), len(rest))
    after_chunk_id = rest[index - 1] if index > 0 else None
    resp = cli.post(
        "/api/backlog/position",
        "POST /backlog/position",
        json_body={"chunk_id": chunk_id, "after_chunk_id": after_chunk_id},
    )
    _, order = cli.decode(resp, _ORDER, "POST /backlog/position")
    landed = next(entry.position for entry in order.entries if entry.chunk_id == chunk_id)
    cli.finish(resp, f"moved {chunk_id} to position {landed}", "POST /backlog/position", answer=_ORDER)

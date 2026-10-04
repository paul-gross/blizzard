"""``blizzard hub events`` — read the operational event feed, every ``/api/events`` filter as a flag."""

from __future__ import annotations

from datetime import datetime
from typing import Any

import click

from blizzard.cli.window import since_option, utc_query_value
from blizzard.hub.cli.command import FleetCommand
from blizzard.hub.cli.context import CliContext
from blizzard.hub.cli.views import Listing


class FeedListing(Listing):
    empty = "no events"

    def line(self, row: Any) -> str:
        runner = f" runner={row['runner_id']}" if row.get("runner_id") else ""
        chunk = f" chunk={row['chunk_id']}" if row.get("chunk_id") else ""
        node = f" node={row['node_name']}" if row.get("node_name") else ""
        return f"{row['recorded_at']}  {row['severity']:<8} {row['kind']}{runner}{chunk}{node}  {row['message']}"


@click.command("events", cls=FleetCommand)
@click.option(
    "--severity", type=click.Choice(["info", "warning", "critical"]), default=None, help="Only this severity."
)
@click.option("--runner", "runner_id", default=None, help="Only events from this runner.")
@click.option("--chunk", "chunk_id", default=None, help="Only events about this chunk.")
@since_option()
@click.option("--limit", type=click.IntRange(1, 200), default=None, help="Max rows (1-200, default 200).")
def events(
    cli: CliContext,
    severity: str | None,
    runner_id: str | None,
    chunk_id: str | None,
    since: datetime | None,
    limit: int | None,
) -> None:
    """Read the operational event feed — the event log unified with open escalations,
    newest first. ``--json`` prints the raw response, ``detail`` included."""
    named = {
        "severity": severity,
        "runner_id": runner_id,
        "chunk_id": chunk_id,
        "since": utc_query_value(since),
        "limit": str(limit) if limit is not None else None,
    }
    params = {k: v for k, v in named.items() if v is not None}
    body = cli.get("/api/events", "GET /events", params=params).json()
    cli.show(body, FeedListing(body["events"]))

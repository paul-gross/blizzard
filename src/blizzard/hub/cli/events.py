"""``blizzard hub events`` — read the operational event feed, every ``/api/events`` filter as a flag."""

from __future__ import annotations

from datetime import datetime
from typing import Any, cast

import click

from blizzard.cli.window import since_option, utc_query_value
from blizzard.foundation.event_log import EventLogSeverity
from blizzard.foundation.untyped import as_table
from blizzard.hub.cli.command import FleetCommand
from blizzard.hub.cli.context import CliContext
from blizzard.hub.cli.project_option import project_option, project_query
from blizzard.hub.cli.views import Listing


class FeedListing(Listing):
    empty = "no events"

    def line(self, row: Any) -> str:
        runner = f" runner={row['runner_id']}" if row.get("runner_id") else ""
        chunk = f" chunk={row['chunk_id']}" if row.get("chunk_id") else ""
        node = f" node={row['node_name']}" if row.get("node_name") else ""
        # An escalation row carries its explanation as `detail.detail`; other rows' payloads stay JSON-only.
        payload = row.get("detail")
        explained = cast(dict[str, object], payload).get("detail") if isinstance(payload, dict) else None
        why = f" ({explained})" if isinstance(explained, str) and explained else ""
        return f"{row['recorded_at']}  {row['severity']:<8} {row['kind']}{runner}{chunk}{node}  {row['message']}{why}"


@click.command("events", cls=FleetCommand)
@click.option(
    "--severity",
    type=click.Choice([severity.value for severity in EventLogSeverity]),
    default=None,
    help="Only this severity.",
)
@click.option("--runner", "runner_id", default=None, help="Only events from this runner.")
@click.option("--chunk", "chunk_id", default=None, help="Only events about this chunk.")
@since_option()
@click.option("--limit", type=click.IntRange(1, 200), default=None, help="Max rows (1-200, default 200).")
@project_option(required_by_hub=False)
def events(
    cli: CliContext,
    severity: str | None,
    runner_id: str | None,
    chunk_id: str | None,
    since: datetime | None,
    limit: int | None,
    project: str | None,
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
    params = {k: v for k, v in named.items() if v is not None} | project_query(project)
    body = cli.get("/api/events", "GET /events", params=params).json()
    cli.show(body, FeedListing(body["events"]))

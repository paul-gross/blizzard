"""``blizzard hub egress`` — operator verbs over the fact-egress export."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import click
import httpx

from blizzard.cli.window import refuse_future_until, since_option, until_option, utc_query_value
from blizzard.foundation.roles import dto
from blizzard.foundation.store.utc import iso_utc
from blizzard.hub.cli.command import FleetCommand
from blizzard.hub.cli.context import CliContext

#: A backfill runs inside the request, and a week-long window outlasts the default client timeout.
_BACKFILL_TIMEOUT = 600.0


@dto
@dataclass(frozen=True)
class StatusView:
    status: dict[str, Any]

    def lines(self) -> list[str]:
        s = self.status
        if s["state"] == "off":
            return ["egress: off"]
        if s["state"] == "rejected":
            return [f"egress: off — {s['rejected_setting']}={s['rejected_value']!r} is not available"]
        lines = [f"egress: on, writing {s['format']} to {s['directory']}"]
        if s["rejected_setting"] is not None:
            lines.append(f"events: off — {s['rejected_setting']}={s['rejected_value']!r} names no key")
        for dataset in s["datasets"]:
            cursor = dataset["cursor_at"] or "none yet"
            lag = "none waiting" if dataset["lag_seconds"] is None else f"{dataset['lag_seconds']:.0f}s"
            lines.append(f"{dataset['name']}: cursor {cursor}  lag: {lag}")
        if s["last_pass_at"] is None:
            lines.append("last pass: none")
        else:
            lines.append(f"last pass: {s['last_pass_at']}  ({s['last_pass_dataset']}, {s['last_pass_row_count']} rows)")
        lines.append(f"last file: {s['last_file'] or 'none'}")
        if s["last_error_at"] is None:
            lines.append("last error: none")
        else:
            ongoing = "ongoing" if s["last_error_ongoing"] else "recovered"
            lines.append(f"last error: {s['last_error_at']}  ({ongoing})  {s['last_error_message']}")
        free = "unreadable" if s["free_bytes"] is None else f"{s['free_bytes']} bytes"
        lines.append(f"free space: {free}  (min {s['min_free_bytes']} bytes)")
        return lines


@click.group("egress")
def egress_group() -> None:
    """Operator verbs over the fact-egress export."""


@egress_group.command("status", cls=FleetCommand)
def egress_status(cli: CliContext) -> None:
    """Whether the export is on, its directory and format, each dataset's cursor and lag, the last pass and
    file, the last error, and the free space."""
    status = cli.get("/api/egress/status", "GET /egress/status").json()
    cli.show(status, StatusView(status))


@egress_group.command("reset", cls=FleetCommand)
@click.option("--dataset", required=True, help="The dataset whose cursor moves: steps, invocations or events.")
@click.option(
    "--to",
    "to",
    type=click.DateTime(),
    required=True,
    help="The instant the cursor moves to, read in the caller's own local time; not in the future.",
)
def egress_reset(cli: CliContext, dataset: str, to: datetime) -> None:
    """Move one dataset's cursor to --to. Forward skips the window between and never exports it; back repeats it,
    writing its rows again to new files. The move is recorded as an egress-cursor-reset event."""
    body = {"dataset": dataset, "to": iso_utc(to.astimezone(UTC))}
    resp = cli.send("post", "/api/egress/reset", json_body=body)
    cli.check(resp, "POST /egress/reset", on_status={409: "the egress export is not configured", 422: "reset refused"})
    result = resp.json()
    was = result["from_at"] or "no cursor yet"
    cli.show_lines(
        result,
        f"{result['dataset']} cursor moved from {was} to {result['to_at']}; the window was {result['direction']}",
    )


@egress_group.command("backfill", cls=FleetCommand)
@since_option(required=True)
@until_option(required=True)
@click.option(
    "--dataset", default=None, help="Only this dataset: steps, invocations or events. Default: every configured one."
)
@click.option("--dry-run", is_flag=True, default=False, help="Count what would be written; write nothing.")
def egress_backfill(cli: CliContext, since: datetime, until: datetime, dataset: str | None, dry_run: bool) -> None:
    """Write the rows of [since, until) again, as the live export would have, without moving a cursor. The files
    carry backfill in their names, each row lands in its own date's partition, and a loader keeps the copy with the
    latest exported_at. A window over backfill_max_window is refused, not split."""
    refuse_future_until(until)
    body = {"since": utc_query_value(since), "until": utc_query_value(until), "dataset": dataset, "dry_run": dry_run}
    resp = cli.send("post", "/api/egress/backfill", json_body=body, timeout=_BACKFILL_TIMEOUT)
    if resp.status_code == httpx.codes.BAD_GATEWAY:
        failure = resp.json()
        placed = ", ".join(f"{c['rows']} {c['dataset']} rows in {c['files']} files" for c in failure["datasets"])
        raise click.ClickException(
            f"{failure['detail']} ({failure['cause']}; committed {placed or 'nothing'} before it stopped)"
        )
    cli.check(
        resp, "POST /egress/backfill", on_status={409: "the egress export is not configured", 422: "window refused"}
    )
    result = resp.json()
    verb = "would write" if dry_run else "wrote"
    lines = [f"{verb} {c['rows']} {c['dataset']} rows in {c['files']} files" for c in result["datasets"]]
    cli.show_lines(result, *lines)

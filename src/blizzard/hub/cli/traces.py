"""``blizzard hub traces`` — operator verbs over fleet-trace export."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

import click
import httpx

from blizzard.cli.window import refuse_future_until, replay_windows, resume_since, since_option, until_option
from blizzard.foundation.clock import SystemClock
from blizzard.foundation.roles import dto
from blizzard.foundation.store.utc import iso_utc
from blizzard.hub.cli.command import FleetCommand
from blizzard.hub.cli.context import CliContext

#: A replay runs inside the request, and a week-long window outlasts the default client timeout.
_REPLAY_TIMEOUT = 600.0

_COLLECTOR_HINT = "point the hub at an OpenTelemetry Collector that receives OTLP over HTTP, and fan out from there"


@dto
@dataclass(frozen=True)
class StatusView:
    status: dict[str, Any]

    def lines(self) -> list[str]:
        s = self.status
        if s["state"] == "enabled":
            lines = [f"tracing: on, exporting to {s['endpoint']}"]
        elif s["state"] == "rejected":
            lines = [
                f"tracing: off — {s['rejected_setting']}={s['rejected_value']!r} is not supported",
                _COLLECTOR_HINT,
            ]
        else:
            return ["tracing: off"]
        cursor = s["cursor_at"] or "none yet"
        lag = "none waiting" if s["lag_seconds"] is None else f"{s['lag_seconds']:.0f}s"
        lines.append(f"cursor: {cursor}  lag: {lag}")
        if s["last_export_at"] is None:
            lines.append("last export: none")
        else:
            lines.append(f"last export: {s['last_export_at']}  ({s['last_export_span_count']} spans)")
        if s["last_error_at"] is None:
            lines.append("last error: none")
        else:
            ongoing = "ongoing" if s["last_error_ongoing"] else "recovered"
            lines.append(f"last error: {s['last_error_at']}  ({ongoing})  {s['last_error_message']}")
        return lines


@click.group("traces")
def traces_group() -> None:
    """Operator verbs over fleet-trace export."""


@traces_group.command("status", cls=FleetCommand)
def traces_status(cli: CliContext) -> None:
    """Whether tracing is on, the endpoint's origin, the cursor and its lag, the last export, and the
    last error. A credential in the endpoint is never shown."""
    status = cli.get("/api/traces/status", "GET /traces/status").json()
    cli.show(status, StatusView(status))


@traces_group.command("replay", cls=FleetCommand)
@since_option(required=True)
@until_option(required=True)
@click.option("--dry-run", is_flag=True, default=False, help="Count what would be told; export nothing.")
def traces_replay(cli: CliContext, since: datetime, until: datetime, dry_run: bool) -> None:
    """Tell every step that closed and chunk that finished in [since, until) again, with the live sweep's span
    ids. The live cursor does not move, so spans the backend already holds arrive again — it dedupes on their
    ids. A range over replay_max_window is told in windows; a failure names the --since to resume from."""
    refuse_future_until(until, SystemClock())
    status = cli.get("/api/traces/status", "GET /traces/status").json()
    windows = replay_windows(since, until, status.get("replay_max_window_seconds"))
    total = {"steps": 0, "chunks": 0, "spans": 0, "batches": 0, "windows": 0}
    for number, (start, stop) in enumerate(windows, 1):
        body = {"since": iso_utc(start), "until": iso_utc(stop), "dry_run": dry_run}
        resume = f"; resume with --since {resume_since(start)}"
        try:
            resp = cli.send("post", "/api/traces/replay", json_body=body, timeout=_REPLAY_TIMEOUT)
        except click.ClickException as exc:
            raise click.ClickException(f"{exc.message} (window {number} of {len(windows)}){resume}") from exc
        if resp.status_code == httpx.codes.BAD_GATEWAY:
            failure = resp.json()
            raise click.ClickException(
                f"{failure['detail']} (told {failure['steps']} steps, {failure.get('chunks', 0)} chunks, "
                f"{failure['spans']} spans in {failure['batches']} batches of window {number} of {len(windows)} "
                f"before it stopped){resume}"
            )
        try:
            cli.check(resp, "POST /traces/replay", on_status={409: "tracing is off", 422: "window refused"})
        except click.ClickException as exc:
            raise click.ClickException(f"{exc.message} (window {number} of {len(windows)}){resume}") from exc
        result = resp.json()
        for key in ("steps", "chunks", "spans", "batches"):
            total[key] += result.get(key, 0)
        total["windows"] = number
        if len(windows) > 1 and not cli.as_json:
            click.echo(
                f"window {number} of {len(windows)} [{iso_utc(start)}, {iso_utc(stop)}): "
                f"{result['steps']} steps, {result.get('chunks', 0)} chunks, {result['spans']} spans",
                err=True,
            )
    verb = "would tell" if dry_run else "told"
    cli.show_lines(
        {**total, "dry_run": dry_run},
        f"{verb} {total['steps']} steps, {total['chunks']} chunks, {total['spans']} spans "
        f"in {total['batches']} batches",
    )

"""``blizzard hub traces`` — operator verbs over fleet-trace export."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import click

from blizzard.hub.cli.command import FleetCommand
from blizzard.hub.cli.context import CliContext

_COLLECTOR_HINT = "point the hub at an OpenTelemetry Collector that receives OTLP over HTTP, and fan out from there"


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

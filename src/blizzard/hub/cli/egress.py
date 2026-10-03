"""``blizzard hub egress`` — operator verbs over the fact-egress export."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import click

from blizzard.hub.cli.command import FleetCommand
from blizzard.hub.cli.context import CliContext


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

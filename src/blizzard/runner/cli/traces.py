"""``blizzard runner traces`` — operator verbs over the runner's lease-trace export; pure clients of the local API."""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any

import click
import httpx

from blizzard.cli.window import replay_windows, resume_since, since_option, until_option
from blizzard.foundation.store.utc import iso_utc
from blizzard.runner.cli.daemon import RunnerDaemon
from blizzard.runner.cli.env import DEFAULT_DIR, ENV_RUNNER_DIR

# The local API's own door override; `control` declares the same variable.
_ENV_LOCAL_API_URL = "BZ_RUNNER_URL"
#: A replay runs inside the request, and a week-long window outlasts the default local-client timeout.
_REPLAY_TIMEOUT = 600.0

_COLLECTOR_HINT = "point the runner at an OpenTelemetry Collector that receives OTLP over HTTP, and fan out from there"


def _daemon_options(command: Any) -> Any:
    command = click.option(
        "--runner-url",
        "runner_url",
        default=None,
        envvar=_ENV_LOCAL_API_URL,
        help="Runner local API over TCP (overrides $BZ_RUNNER_URL).",
    )(command)
    return click.option(
        "--dir",
        "directory",
        default=DEFAULT_DIR,
        envvar=ENV_RUNNER_DIR,
        help="Runner runtime directory (overrides $BZ_RUNNER_DIR).",
    )(command)


_OUTCOME_TEXT = {
    "captured": "captured",
    "operator_configured": "operator-configured (not captured)",
    "no_runner_destination": "no runner destination (not captured)",
    "off": "off",
}
_SIGNAL_UNIT = {"traces": "spans", "metrics": "data points", "logs": "log records"}


def harness_telemetry_lines(harness: dict[str, Any] | None) -> list[str]:
    """The ``harness telemetry`` section: one line per signal, its outcome and, where captured, what the
    runner's receiver has taken in; a single ``off`` line where every signal is off. Empty where the runner
    reported no plan."""
    if harness is None:
        return []
    if all(harness[signal]["outcome"] == "off" for signal in _SIGNAL_UNIT):
        return ["harness telemetry: off"]
    lines = ["harness telemetry:"]
    for signal, unit in _SIGNAL_UNIT.items():
        entry = harness[signal]
        line = f"  {signal}: {_OUTCOME_TEXT[entry['outcome']]}"
        if entry["outcome"] == "captured":
            line += f"  ({entry['accepted']} {unit} accepted, {entry['dropped']} dropped)"
        lines.append(line)
    return lines


def _status_lines(s: dict[str, Any]) -> list[str]:
    if s["state"] == "enabled":
        lines = [f"tracing: on, exporting to {s['endpoint']}"]
    elif s["state"] == "rejected":
        return [
            f"tracing: off — {s['rejected_setting']}={s['rejected_value']!r} is not supported",
            _COLLECTOR_HINT,
            *harness_telemetry_lines(s.get("harness_telemetry")),
        ]
    else:
        return ["tracing: off", *harness_telemetry_lines(s.get("harness_telemetry"))]
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
    receiver = s.get("receiver")
    if receiver is not None:
        lines.append(f"worker spans: {receiver['accepted_spans']} accepted, {receiver['dropped_spans']} dropped")
    return [*lines, *harness_telemetry_lines(s.get("harness_telemetry"))]


@click.group("traces")
def traces_group() -> None:
    """Operator verbs over the runner's lease-trace export."""


@traces_group.command("status")
@click.option("--json", "as_json", is_flag=True, default=False, help="Print the response body as JSON.")
@_daemon_options
def traces_status(as_json: bool, directory: str, runner_url: str | None) -> None:
    """Whether tracing is on, the endpoint's origin, the cursor and its lag, the last export, and the
    last error. A credential in the endpoint is never shown."""
    with RunnerDaemon.reach("traces status", directory, runner_url) as daemon:
        status = daemon.get("/api/traces/status").json()
    if as_json:
        click.echo(json.dumps(status, indent=2))
        return
    for line in _status_lines(status):
        click.echo(line)


@traces_group.command("replay")
@since_option(required=True)
@until_option(required=True)
@click.option("--dry-run", is_flag=True, default=False, help="Count what would be told; export nothing.")
@click.option("--json", "as_json", is_flag=True, default=False, help="Print the response body as JSON.")
@_daemon_options
def traces_replay(
    since: datetime, until: datetime, dry_run: bool, as_json: bool, directory: str, runner_url: str | None
) -> None:
    """Tell every lease that closed in [since, until) again, with the live sweep's span ids. The live cursor
    does not move, so spans the backend already holds arrive again — it dedupes on their ids. A range over
    replay_max_window is told in windows; a failure names the --since to resume from."""
    total = {"leases": 0, "spans": 0, "batches": 0, "windows": 0}
    with RunnerDaemon.reach("traces replay", directory, runner_url) as daemon:
        status = daemon.get("/api/traces/status").json()
        windows = replay_windows(since, until, status.get("replay_max_window_seconds"))
        for number, (start, stop) in enumerate(windows, 1):
            body = {"since": iso_utc(start), "until": iso_utc(stop), "dry_run": dry_run}
            where = f"window {number} of {len(windows)}; resume with --since {resume_since(start)}"
            try:
                resp = daemon.client.post("/api/traces/replay", json=body, timeout=_REPLAY_TIMEOUT)
            except httpx.HTTPError as exc:
                raise click.ClickException(f"{daemon.unreachable(exc).message} ({where})") from exc
            if resp.status_code == httpx.codes.BAD_GATEWAY:
                failure = resp.json()
                raise click.ClickException(
                    f"{failure['detail']} (told {failure['leases']} leases, {failure['spans']} spans "
                    f"in {failure['batches']} batches before it stopped; {where})"
                )
            if resp.status_code in (httpx.codes.CONFLICT, httpx.codes.UNPROCESSABLE_ENTITY):
                reason = "tracing is off" if resp.status_code == httpx.codes.CONFLICT else "window refused"
                raise click.ClickException(f"{reason}: {resp.json().get('detail', '')} ({where})")
            try:
                resp.raise_for_status()
            except httpx.HTTPError as exc:
                raise click.ClickException(f"{daemon.unreachable(exc).message} ({where})") from exc
            result = resp.json()
            for key in ("leases", "spans", "batches"):
                total[key] += result[key]
            total["windows"] = number
            if len(windows) > 1 and not as_json:
                click.echo(
                    f"window {number} of {len(windows)} [{iso_utc(start)}, {iso_utc(stop)}): "
                    f"{result['leases']} leases, {result['spans']} spans",
                    err=True,
                )
    if as_json:
        click.echo(json.dumps({**total, "dry_run": dry_run}, indent=2))
        return
    verb = "would tell" if dry_run else "told"
    click.echo(f"{verb} {total['leases']} leases, {total['spans']} spans in {total['batches']} batches")

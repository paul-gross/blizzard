"""``blizzard runner traces`` — operator verbs over the runner's lease-trace export; pure clients of the local API."""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any

import click
import httpx

from blizzard.cli.window import since_option, until_option, utc_query_value
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


def _status_lines(s: dict[str, Any]) -> list[str]:
    if s["state"] == "enabled":
        lines = [f"tracing: on, exporting to {s['endpoint']}"]
    elif s["state"] == "rejected":
        return [f"tracing: off — {s['rejected_setting']}={s['rejected_value']!r} is not supported", _COLLECTOR_HINT]
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
    does not move, so spans the backend already holds arrive again — it dedupes on their ids. The window is
    bounded by the runner's replay_max_window."""
    body = {"since": utc_query_value(since), "until": utc_query_value(until), "dry_run": dry_run}
    with RunnerDaemon.reach("traces replay", directory, runner_url) as daemon:
        resp = daemon.client.post("/api/traces/replay", json=body, timeout=_REPLAY_TIMEOUT)
    if resp.status_code == httpx.codes.BAD_GATEWAY:
        failure = resp.json()
        raise click.ClickException(
            f"{failure['detail']} (told {failure['leases']} leases, {failure['spans']} spans "
            f"in {failure['batches']} batches before it stopped)"
        )
    if resp.status_code in (httpx.codes.CONFLICT, httpx.codes.UNPROCESSABLE_ENTITY):
        reason = "tracing is off" if resp.status_code == httpx.codes.CONFLICT else "window refused"
        raise click.ClickException(f"{reason}: {resp.json().get('detail', '')}")
    resp.raise_for_status()
    result = resp.json()
    if as_json:
        click.echo(json.dumps(result, indent=2))
        return
    verb = "would tell" if dry_run else "told"
    click.echo(f"{verb} {result['leases']} leases, {result['spans']} spans in {result['batches']} batches")

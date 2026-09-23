"""``blizzard runner analytics`` — a worker's own routine-run read of the fleet-wide file,
skill, agent-type, and node usage counts, and the per-node and per-graph spend summaries,
over a window it names (blizzard#545)."""

from __future__ import annotations

from datetime import datetime

import click

from blizzard.cli.window import since_option, until_option, utc_query_value
from blizzard.runner.cli.worker_call import WorkerCall


@click.group("analytics")
def analytics_group() -> None:
    """Worker: read fleet-wide usage-counts and spend summaries over a window — access
    is gated server-side on the worker's own lease naming a routine-run chunk, but the
    rows returned are fleet-wide rollups, not scoped to that run."""


@analytics_group.group("counts")
def counts_group() -> None:
    """Worker: read one of the usage-counts summaries."""


@analytics_group.group("spend")
def spend_group() -> None:
    """Worker: read one of the spend summaries."""


def _window_params(since: datetime, until: datetime | None) -> dict[str, str]:
    since_value = utc_query_value(since)
    assert since_value is not None  # --since is a required option
    params = {"since": since_value}
    until_value = utc_query_value(until)
    if until_value is not None:
        params["until"] = until_value
    return params


def _read(verb: str, path: str, since: datetime, until: datetime | None) -> None:
    worker = WorkerCall.of(verb)
    resp = worker.get(
        worker.leased(f"analytics/{path}"), failure=f"could not read {path}", params=_window_params(since, until)
    )
    click.echo(resp.text)


@counts_group.command("files")
@since_option(required=True)
@until_option()
def counts_files(since: datetime, until: datetime | None) -> None:
    """Worker: file-read occurrence counts over the window, as JSON."""
    _read("analytics counts files", "counts/files", since, until)


@counts_group.command("skills")
@since_option(required=True)
@until_option()
def counts_skills(since: datetime, until: datetime | None) -> None:
    """Worker: skill-invocation occurrence counts over the window, as JSON."""
    _read("analytics counts skills", "counts/skills", since, until)


@counts_group.command("agent-types")
@since_option(required=True)
@until_option()
def counts_agent_types(since: datetime, until: datetime | None) -> None:
    """Worker: occurrence counts by agent type over the window, as JSON."""
    _read("analytics counts agent-types", "counts/agent-types", since, until)


@counts_group.command("nodes")
@since_option(required=True)
@until_option()
def counts_nodes(since: datetime, until: datetime | None) -> None:
    """Worker: occurrence counts by node over the window, as JSON."""
    _read("analytics counts nodes", "counts/nodes", since, until)


@spend_group.command("nodes")
@since_option(required=True)
@until_option()
def spend_nodes(since: datetime, until: datetime | None) -> None:
    """Worker: usage/cost rollups by node over the window, as JSON."""
    _read("analytics spend nodes", "spend/nodes", since, until)


@spend_group.command("graphs")
@since_option(required=True)
@until_option()
def spend_graphs(since: datetime, until: datetime | None) -> None:
    """Worker: usage/cost rollups by graph over the window, as JSON."""
    _read("analytics spend graphs", "spend/graphs", since, until)

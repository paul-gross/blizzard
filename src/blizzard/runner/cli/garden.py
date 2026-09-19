"""``blizzard runner garden`` — a worker's own routine's live finding bucket (D4) and open
garden-proposal docket."""

from __future__ import annotations

import click

from blizzard.runner.cli.worker_call import WorkerCall


@click.group("garden")
def garden_group() -> None:
    """Worker: read this run's own garden machinery — the routine and scope are both
    derived server-side from the worker's own lease, so no verb here takes a flag
    naming either."""


@garden_group.command("findings")
def garden_findings() -> None:
    """Worker: list this run's live finding bucket as JSON."""
    worker = WorkerCall.of("garden findings")
    resp = worker.get(worker.leased("garden/findings"), failure="could not read the finding bucket")
    click.echo(resp.text)


@garden_group.command("proposals")
def garden_proposals() -> None:
    """Worker: list this run's own routine's open garden proposals as JSON, closed ones
    excluded."""
    worker = WorkerCall.of("garden proposals")
    resp = worker.get(worker.leased("garden/proposals"), failure="could not read the proposal docket")
    click.echo(resp.text)

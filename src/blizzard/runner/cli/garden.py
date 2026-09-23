"""``blizzard runner garden`` — a worker's own routine's live-plus-``delivered`` finding
bucket (D4, blizzard#583 D2) and open garden-proposal docket."""

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
    """Worker: list this run's live-plus-``delivered`` finding bucket as JSON."""
    worker = WorkerCall.of("garden findings")
    resp = worker.get(worker.leased("garden/findings"), failure="could not read the finding bucket")
    click.echo(resp.text)


@garden_group.command("proposals")
@click.option(
    "--state",
    type=click.Choice(["open", "closed", "all"]),
    default="open",
    show_default=True,
    help="Which of the routine's proposals to list.",
)
def garden_proposals(state: str) -> None:
    """Worker: list this run's own routine's garden proposals as JSON, filtered by
    STATE — closed ones carry their closure and pass reason."""
    worker = WorkerCall.of("garden proposals")
    resp = worker.get(
        worker.leased("garden/proposals"), failure="could not read the proposal docket", params={"state": state}
    )
    click.echo(resp.text)

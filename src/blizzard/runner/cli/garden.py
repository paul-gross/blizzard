"""``blizzard runner garden`` — a worker's own routine's live-plus-``delivered`` finding
bucket and open garden-proposal docket."""

from __future__ import annotations

import click

from blizzard.runner.cli.container_command import ContainerWorkerCommand
from blizzard.runner.cli.worker_call import WorkerCall, WorkerPolicy


@click.group("garden")
def garden_group() -> None:
    """Worker: read the findings and garden proposals of the routine this run belongs to — always
    this run's own routine and scope, so no verb names either."""


@garden_group.command(
    "findings", cls=ContainerWorkerCommand, collaborators={"worker": WorkerCall}, policy=WorkerPolicy("garden findings")
)
def garden_findings(worker: WorkerCall) -> None:
    """Worker: list this run's finding bucket as JSON — the routine's findings in every scope."""
    resp = worker.get(worker.leased("garden/findings"), failure="could not read the finding bucket")
    click.echo(resp.text)


@garden_group.command(
    "proposals",
    cls=ContainerWorkerCommand,
    collaborators={"worker": WorkerCall},
    policy=WorkerPolicy("garden proposals"),
)
@click.option(
    "--state",
    type=click.Choice(["open", "closed", "all"]),
    default="open",
    show_default=True,
    help="Which of the routine's proposals to list.",
)
def garden_proposals(state: str, worker: WorkerCall) -> None:
    """Worker: list this run's own routine's garden proposals as JSON, filtered by
    STATE — closed ones carry their closure and pass reason."""
    resp = worker.get(
        worker.leased("garden/proposals"), failure="could not read the proposal docket", params={"state": state}
    )
    click.echo(resp.text)

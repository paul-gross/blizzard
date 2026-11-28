"""``blizzard runner scope`` — the deployment's scope vocabulary."""

from __future__ import annotations

import click

from blizzard.runner.cli.container_command import ContainerWorkerCommand
from blizzard.runner.cli.worker_call import WorkerCall, WorkerPolicy


@click.group("scope")
def scope_group() -> None:
    """Worker: read the deployment's scope vocabulary."""


@scope_group.command(
    "list", cls=ContainerWorkerCommand, collaborators={"worker": WorkerCall}, policy=WorkerPolicy("scope list")
)
def scope_list(worker: WorkerCall) -> None:
    """Worker: list every scope (slug, description, retired) as JSON."""
    resp = worker.get(worker.leased("scopes"), failure="could not read the scope list")
    click.echo(resp.text)

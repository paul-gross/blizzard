"""A worker command reading its lease's history."""

# exemplar: bzh:dependency-injection — the callback receives only the collaborator it consumes.

from __future__ import annotations

import click

from blizzard.runner.cli.container_command import ContainerWorkerCommand
from blizzard.runner.cli.worker_call import WorkerPolicy
from exemplar.runner.cli.wiring import HistoryReader, register


@click.command(
    cls=ContainerWorkerCommand,
    collaborators={"reader": HistoryReader},
    area=register,
    policy=WorkerPolicy(verb="history", lease=True, traced=True),
)
def history(reader: HistoryReader) -> None:
    """Read this lease's history."""
    reader.show()

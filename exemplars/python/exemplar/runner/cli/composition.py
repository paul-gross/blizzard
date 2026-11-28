"""A stand-in runner group owning the worker session."""

# exemplar: bzh:dependency-injection — production adds the command to the runner group's lazy map.

from __future__ import annotations

import click

from blizzard.cli.collaborators import CliCollaborators
from blizzard.cli.operator_trace import OperatorGroup
from blizzard.runner.cli.worker_call import WorkerSession


class RunnerGroup(OperatorGroup):
    def invoke(self, ctx: click.Context) -> object:
        return WorkerSession.begin(ctx, CliCollaborators.of(ctx)).run(lambda: super(RunnerGroup, self).invoke(ctx))


@click.group(cls=RunnerGroup, lazy={"history": "exemplar.runner.cli.history:history"}, trace_root="runner")
def runner() -> None:
    """Talk to the runner."""

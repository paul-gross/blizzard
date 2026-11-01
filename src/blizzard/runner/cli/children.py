"""The runner CLI's carrier — the shared collaborators extended with the launch seam and the opencode loopback
transport (``bzh:dependency-injection``)."""

from __future__ import annotations

from dataclasses import dataclass

import click

from blizzard.cli.collaborators import CliCollaborators
from blizzard.runner.harness.opencode.compatibility.loopback import ILoopbackTransport, UrllibLoopbackTransport
from blizzard.runner.process.child_processes import IChildProcesses
from blizzard.runner.process.internal.subprocess_child_processes import SubprocessChildProcesses

_PIN = "blizzard.runner.cli.children"


@dataclass(frozen=True)
class RunnerCliCollaborators(CliCollaborators):
    """What a runner verb acts through: the shared clocks and HTTP factory, plus the child-process launcher and
    the opencode loopback transport. An invoker hands its own to the root group as ``obj``; with none, the
    production ones are built."""

    children: IChildProcesses
    loopback: ILoopbackTransport

    @classmethod
    def of(cls, ctx: click.Context) -> RunnerCliCollaborators:
        """The invoker's carrier, else the shared collaborators extended with the production launcher and transport.

        The first resolution is pinned on the chain's shared ``meta``, so it survives a group replacing ``ctx.obj``."""
        pinned: RunnerCliCollaborators | None = ctx.meta.get(_PIN)
        if pinned is None:
            pinned = ctx.meta[_PIN] = ctx.find_object(cls) or cls.extending(CliCollaborators.of(ctx))
        return pinned

    @classmethod
    def extending(cls, shared: CliCollaborators) -> RunnerCliCollaborators:
        return cls(
            client_factory=shared.client_factory,
            clock=shared.clock,
            wall_clock=shared.wall_clock,
            monotonic_clock=shared.monotonic_clock,
            children=SubprocessChildProcesses(),
            loopback=UrllibLoopbackTransport(),
        )


def command_children(ctx: click.Context) -> IChildProcesses:
    return RunnerCliCollaborators.of(ctx).children

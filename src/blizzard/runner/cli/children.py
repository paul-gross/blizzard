"""The runner CLI's carrier — the shared collaborators extended with the launch seam, the opencode loopback
transport, and the OpenCode 2 service transport (``bzh:dependency-injection``)."""

from __future__ import annotations

from dataclasses import dataclass

import click

from blizzard.cli.collaborators import CliCollaborators
from blizzard.runner.harness.binding_selection import IVersionProbe
from blizzard.runner.harness.internal.version_probe import VersionProbe
from blizzard.runner.harness.opencode.compatibility.loopback import ILoopbackTransport
from blizzard.runner.harness.opencode_v2.transport import IServiceTransport
from blizzard.runner.process.child_env import ChildEnv
from blizzard.runner.process.child_processes import IChildProcesses

_PIN = "blizzard.runner.cli.children"


@dataclass(frozen=True)
class RunnerCliCollaborators(CliCollaborators):
    """What a runner verb acts through: the shared clocks and HTTP factory, plus the child-process launcher,
    the opencode loopback transport, and the OpenCode 2 service transport. An invoker hands its own to the root
    group as ``obj``; with none, the production ones are built."""

    children: IChildProcesses
    loopback: ILoopbackTransport
    service: IServiceTransport

    @classmethod
    def of(cls, ctx: click.Context) -> RunnerCliCollaborators:
        """The invoker's carrier, else the shared collaborators extended with the production launcher and transport.

        The first resolution is pinned on the chain's shared ``meta``, so it survives a group replacing ``ctx.obj``."""
        pinned: RunnerCliCollaborators | None = ctx.meta.get(_PIN)
        if pinned is None:
            pinned = ctx.meta[_PIN] = ctx.find_object(cls) or cls.extending(CliCollaborators.of(ctx))
        return pinned

    @classmethod
    def provided(cls, ctx: click.Context) -> RunnerCliCollaborators | None:
        """The supplied or already pinned carrier, without constructing production defaults."""
        return ctx.meta.get(_PIN) or ctx.find_object(cls)

    @classmethod
    def extending(cls, shared: CliCollaborators) -> RunnerCliCollaborators:
        from blizzard.runner.harness.opencode.compatibility.wiring import loopback_transport
        from blizzard.runner.harness.opencode_v2.transport_wiring import service_transport
        from blizzard.runner.process.wiring import child_processes

        return cls(
            client_factory=shared.client_factory,
            clock=shared.clock,
            wall_clock=shared.wall_clock,
            monotonic_clock=shared.monotonic_clock,
            children=child_processes(shared.monotonic_clock),
            loopback=loopback_transport(),
            service=service_transport(),
        )


def command_children(ctx: click.Context) -> IChildProcesses:
    return RunnerCliCollaborators.of(ctx).children


def command_versions(ctx: click.Context, env: ChildEnv) -> IVersionProbe:
    """The version probe a verb selects bindings through: each binary's ``--version`` run under ``env`` by the
    carrier's launcher."""
    return VersionProbe(command_children(ctx), env)

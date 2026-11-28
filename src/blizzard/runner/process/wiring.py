"""Production child-process launcher for runner command composition."""

from blizzard.foundation.clock import IMonotonicClock
from blizzard.foundation.registry.interface import Lifetime, Registry, port
from blizzard.runner.process.child_processes import IChildProcesses
from blizzard.runner.process.internal.subprocess_child_processes import SubprocessChildProcesses


def child_processes(clock: IMonotonicClock) -> IChildProcesses:
    return SubprocessChildProcesses(clock)


CLOCK = port(IMonotonicClock)


def register(registry: Registry) -> None:
    registry.context(CLOCK, lifetime=Lifetime.COMMAND)
    registry.factory(child_processes, lifetime=Lifetime.COMMAND)

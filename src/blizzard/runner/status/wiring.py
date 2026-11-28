"""The host's invariant check over the runner-store checker."""

from blizzard.foundation.registry.interface import Lifetime, Ref, Registry, port
from blizzard.runner.status.invariants import HostInvariantCheck, IRunnerInvariantChecker


def register_host_infrastructure(registry: Registry, checker: Ref[IRunnerInvariantChecker]) -> None:
    registry.alias(port(IRunnerInvariantChecker), checker)
    registry.service(HostInvariantCheck, lifetime=Lifetime.PROCESS)

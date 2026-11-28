"""Named leaves for runner assembly still owned by the legacy composer."""

from __future__ import annotations

from typing import Any

from blizzard.foundation.clock import IClock
from blizzard.foundation.registry.interface import Lifetime, Ref, port
from blizzard.foundation.registry.legacy import LegacyRegistry
from blizzard.runner.composition import HostProcess, HostRunnerSet, RunnerCompose, RunnerProcess
from blizzard.runner.host_config import BuiltRunner, HostConfig
from blizzard.runner.throttle.usage_limits import IWriteSubscriptionUsageLimitRepository
from blizzard.runner.tracing.runner_export import RunnerTraceExport


class LegacyHostRef:
    """The legacy runner assembler's host, attached after host infrastructure opens."""

    def __init__(self) -> None:
        self.host: HostProcess | None = None

    def attach(self, host: HostProcess) -> None:
        if self.host is host:
            return
        if self.host is not None:
            raise RuntimeError("the legacy host is already attached")
        self.host = host


def legacy_host(reference: LegacyHostRef) -> HostProcess:
    assert reference.host is not None
    return reference.host


def legacy_runner_set(host: HostProcess) -> HostRunnerSet[Any, Any]:
    return HostRunnerSet(host)


def register_runner_children(registry: LegacyRegistry) -> None:
    registry.service(LegacyHostRef, lifetime=Lifetime.PROCESS)
    registry.leaf(legacy_host, lifetime=Lifetime.PROCESS)
    registry.leaf(legacy_runner_set, lifetime=Lifetime.PROCESS)
    registry.context(port(BuiltRunner), lifetime=Lifetime.RUNNER)
    registry.context(port(HostConfig), lifetime=Lifetime.RUNNER)
    registry.context(port(RunnerCompose), lifetime=Lifetime.RUNNER)
    registry.leaf(registered_runner, lifetime=Lifetime.RUNNER)


def registered_runner(
    built: BuiltRunner,
    host_config: HostConfig,
    host: HostProcess,
    compose: RunnerCompose,
    trace: RunnerTraceExport,
) -> RunnerProcess:
    return compose.runner(host_config, host, built, runner_trace_export=trace)


def composed_runner(
    built: BuiltRunner, host_config: HostConfig, host: HostProcess, compose: RunnerCompose
) -> RunnerProcess:
    return compose.runner(host_config, host, built)


def register_legacy_host(registry: LegacyRegistry) -> Ref[IWriteSubscriptionUsageLimitRepository]:
    """The not-yet-adopted runner assembly over a borrowed host."""
    registry.context(port(HostProcess), lifetime=Lifetime.PROCESS)
    registry.context(port(HostConfig), lifetime=Lifetime.PROCESS)
    registry.context(port(RunnerCompose), lifetime=Lifetime.PROCESS)
    registry.service(HostRunnerSet, lifetime=Lifetime.PROCESS)
    registry.context(port(BuiltRunner), lifetime=Lifetime.RUNNER)
    registry.leaf(composed_runner, lifetime=Lifetime.RUNNER)
    usage_limits = registry.context(port(IWriteSubscriptionUsageLimitRepository), lifetime=Lifetime.PROCESS)
    registry.context(port(IClock), lifetime=Lifetime.PROCESS)
    return usage_limits

"""The runner host's composition root: it registers the host's keys, then opens the validated container over them.

``HostRunnerSet`` owns the runner scopes beneath the process scope; each runner it hosts opens a child scope there,
whose one context value is its :class:`~blizzard.runner.host_config.BuiltRunner`. The process scope borrows the
:class:`~blizzard.runner.composition.HostProcess` it opens with, whose supplier closes it. Registering and building are
separate steps, so a caller declares its runner graph before the build.

What the host and each runner are still built by — the host's process graph and a runner's composition — is reached
through named legacy leaves over the borrowed host; an adopted area depends on a leaf only by the ``Ref`` the root
hands it."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any, cast

from blizzard.foundation.clock import IClock
from blizzard.foundation.registry.interface import Lifetime, port
from blizzard.foundation.registry.internal.recording import RecordingRegistry
from blizzard.foundation.registry.internal.translation import HOST_LIFETIMES, build_container
from blizzard.foundation.registry.legacy import LegacyRegistry
from blizzard.foundation.registry.scopes import OwnedChild, ScopeValues, supply
from blizzard.foundation.roles import collaborator
from blizzard.runner.composition import (
    HostProcess,
    HostRunnerSet,
    IRunnerResume,
    IWiredRunner,
    RunnerCompose,
    RunnerProcess,
)
from blizzard.runner.host_config import BuiltRunner, HostConfig
from blizzard.runner.throttle import wiring as throttle
from blizzard.runner.throttle.usage_limits import IWriteSubscriptionUsageLimitRepository

#: The process scope's owner id.
HOST_OWNER = "host"


def composed_runner(
    built: BuiltRunner, host_config: HostConfig, host: HostProcess, compose: RunnerCompose
) -> RunnerProcess:
    """A runner child's process graph: ``built`` composed over the borrowed host — a legacy leaf, closed by the
    runner set before the child closes."""
    return compose.runner(host_config, host, built)


def host_usage_limits(host: HostProcess) -> IWriteSubscriptionUsageLimitRepository:
    """The host's subscription usage-limit store — a legacy leaf over the borrowed host."""
    return host.host_stores.usage_limits


def host_clock(host: HostProcess) -> IClock:
    """The host's wall clock — a legacy leaf over the borrowed host."""
    return host.clock


def register_host(registry: LegacyRegistry) -> None:
    """Declare the borrowed host, the host-lifetime values a runner child is composed from, the runner set over it,
    the one value a runner child is opened with, the legacy leaves over the host, and each adopted area."""
    registry.context(port(HostProcess), lifetime=Lifetime.PROCESS)
    registry.context(port(HostConfig), lifetime=Lifetime.PROCESS)
    registry.context(port(RunnerCompose), lifetime=Lifetime.PROCESS)
    registry.service(HostRunnerSet, lifetime=Lifetime.PROCESS)
    registry.context(port(BuiltRunner), lifetime=Lifetime.RUNNER)
    registry.leaf(composed_runner, lifetime=Lifetime.RUNNER)
    usage_limits = registry.leaf(host_usage_limits, lifetime=Lifetime.PROCESS)
    registry.leaf(host_clock, lifetime=Lifetime.PROCESS)
    throttle.register(registry, usage_limits)


@collaborator
@dataclass(frozen=True)
class HostRoot[W: IWiredRunner, R: IRunnerResume]:
    """The host's open process scope and the runner set that owns the runner scopes beneath it."""

    owner: OwnedChild
    runners: HostRunnerSet[W, R]

    @classmethod
    def open(
        cls,
        registry: LegacyRegistry,
        host: HostProcess,
        host_config: HostConfig,
        *,
        compose: RunnerCompose | None = None,
        eager: Sequence[type[Any]] = (),
    ) -> HostRoot[W, R]:
        """Open the validated graph ``registry`` declares over the borrowed ``host`` and the ``host_config`` and
        ``compose`` options its runners are composed from, and bind the runner set to open children beneath it;
        ``eager`` are the runner types built when a child opens. ``registry`` already holds :func:`register_host`'s
        keys."""
        values = ScopeValues(
            (
                supply(port(HostProcess), host),
                supply(port(HostConfig), host_config),
                supply(port(RunnerCompose), compose if compose is not None else RunnerCompose()),
            )
        )
        owner = build_container(registry, HOST_LIFETIMES, owner=HOST_OWNER, values=values)
        try:
            runners = cast("HostRunnerSet[W, R]", owner.resolver.get(HostRunnerSet))
            runners.bind(owner, eager=eager)
        except BaseException as failure:
            try:
                owner.close()
            except Exception as cleanup:  # the opening failure is the one reported
                failure.add_note(f"closing the process scope after it also failed: {cleanup!r}")
            raise
        return cls(owner, runners)

    @classmethod
    def serving(
        cls, host: HostProcess, host_config: HostConfig, *, compose: RunnerCompose | None = None
    ) -> HostRoot[W, R]:
        """The production root over ``host``: :func:`register_host`'s graph, each runner child composing its runner
        as it opens, so a bad entry fails the child's opening."""
        registry = LegacyRegistry()
        register_host(registry)
        return cls.open(registry, host, host_config, compose=compose, eager=(RunnerProcess,))

    def close(self) -> None:
        """Close every runner the set holds, then the process scope, attempting the second when the first fails."""
        try:
            self.runners.close_all()
        finally:
            self.owner.close()


def open_host_free() -> OwnedChild:
    """The graph an app built without a host root serves its host routes from: each host service declared absent."""
    registry = RecordingRegistry()
    throttle.register_absent(registry)
    return build_container(registry, HOST_LIFETIMES, owner=HOST_OWNER, values=ScopeValues())

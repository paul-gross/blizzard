"""The runner host's composition root: it registers the host's keys, then opens the validated container over them.

``HostRunnerSet`` owns the runner scopes beneath the process scope; each runner it hosts opens a child scope there,
whose one context value is its :class:`~blizzard.runner.host_config.BuiltRunner`. The process scope borrows the
:class:`~blizzard.runner.composition.HostProcess` it opens with, whose supplier closes it. Registering and building are
separate steps, so a caller declares its runner graph before the build.

What the host and each runner are still built by — the host's process graph and a runner's composition — is reached
through named legacy leaves over the borrowed host; an adopted area depends on a leaf only by the ``Ref`` the root
hands it.

A standalone command driving one runner opens the same root over a host of one it builds, through
:func:`open_standalone`, and owns what it opened."""

from __future__ import annotations

import contextlib
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field, replace
from typing import Any, TypedDict, Unpack, cast

from blizzard.foundation.clock import IClock, IMonotonicClock, SystemClock
from blizzard.foundation.platform_tracing.handle import IPlatformTracing
from blizzard.foundation.registry.interface import Lifetime, port
from blizzard.foundation.registry.internal.recording import RecordingRegistry
from blizzard.foundation.registry.internal.translation import HOST_LIFETIMES, build_container
from blizzard.foundation.registry.legacy import LegacyRegistry
from blizzard.foundation.registry.scopes import OwnedChild, ScopeValues, supply
from blizzard.foundation.roles import collaborator
from blizzard.foundation.trace_export.exporter import ITraceExporter
from blizzard.runner.composition import (
    HostProcess,
    HostRunnerSet,
    IRunnerResume,
    IWiredRunner,
    RunnerCompose,
    RunnerProcess,
    build_host_of_one,
)
from blizzard.runner.events.broker import EventBroker
from blizzard.runner.harness.binding_selection import BindingSelection
from blizzard.runner.harness.bundle import BundleSnapshot
from blizzard.runner.host_config import BuiltRunner, HostConfig
from blizzard.runner.throttle import wiring as throttle
from blizzard.runner.throttle.usage_limits import IWriteSubscriptionUsageLimitRepository
from blizzard.runner.tracing.received_export import IReceivedTelemetryExport

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


@dataclass(frozen=True)
class OpenedHost:
    """A host, the host root opened over it, and the runner children opened beneath that root but not yet served —
    what a served app over a root its caller opened serves from. Closing it closes each runner's graph and its
    child, then the root, then the host, each once."""

    host: HostProcess
    root: HostRoot[Any, Any]
    children: tuple[OwnedChild, ...]
    #: Each child's runner, composed as its child opened, in the children's order.
    runners: tuple[RunnerProcess, ...]
    _closing: contextlib.ExitStack = field(repr=False, compare=False)

    def close(self) -> None:
        self._closing.close()


def open_host(
    host: HostProcess, host_config: HostConfig, runners: Sequence[BuiltRunner], *, compose: RunnerCompose | None = None
) -> OpenedHost:
    """The production root over ``host``, a child opened beneath it for each of ``runners``, its runner composed under
    ``compose`` and held among the host's runners. The opened host owns ``host``: a failure closes what opened, then
    ``host``."""
    with contextlib.ExitStack() as closing:
        closing.callback(host.close)
        root = HostRoot[Any, Any].serving(host, host_config, compose=compose)
        closing.callback(root.close)
        children: list[OwnedChild] = []
        composed: list[RunnerProcess] = []
        for built in runners:
            child = root.runners.open_runner(built)
            if child is None:
                raise RuntimeError(f"runner {built.id}: the host's runners open no child")
            runner = child.resolver.get(RunnerProcess)
            closing.callback(root.runners.close_graph, runner, child)
            children.append(child)
            composed.append(runner)
        host.runners = (*host.runners, *composed)
        return OpenedHost(host, root, tuple(children), tuple(composed), closing.pop_all())


class StandaloneOptions(TypedDict, total=False):
    """What a standalone opening builds its host of one and composes its runner with, beyond the runner itself."""

    events: EventBroker | None
    bundle: BundleSnapshot | None
    selection: BindingSelection | None
    publish_bundle: bool
    environ: Mapping[str, str] | None
    trace_exporter: ITraceExporter | None
    platform_tracing: IPlatformTracing | None
    received_telemetry: IReceivedTelemetryExport | None
    #: The host's own credential, so a runner of a registered host leaves its roster to the host.
    host_token: str
    monotonic_clock: IMonotonicClock | None


def open_standalone(host_config: HostConfig, built: BuiltRunner, **options: Unpack[StandaloneOptions]) -> OpenedHost:
    """A host of one for ``built``, from its config, and the host root opened over it with ``built``'s child beneath
    it, for the standalone commands that drive one runner. The runner is composed with no settings reader: the one
    verb that reads its settings attaches its own."""
    environ = options.get("environ")
    host = build_host_of_one(
        built.config,
        clock=SystemClock(),
        events=options.get("events"),
        environ=environ,
        platform_tracing=options.get("platform_tracing"),
        received_telemetry=options.get("received_telemetry"),
        host_token=options.get("host_token", ""),
        monotonic_clock=options.get("monotonic_clock"),
    )
    selection = options.get("selection")
    compose = RunnerCompose(
        publish=frozenset({built.id}) if options.get("publish_bundle", False) else frozenset(),
        bundles={built.id: options.get("bundle")},
        selections={built.id: selection} if selection is not None else {},
        environ=environ,
        trace_exporter=options.get("trace_exporter"),
        settings=False,
    )
    return open_host(host, host_config, (built,), compose=compose)


def build_runner_process(
    host_config: HostConfig, built: BuiltRunner, **options: Unpack[StandaloneOptions]
) -> RunnerProcess:
    """``built``'s runner on a host of one, opened by :func:`open_standalone` under ``options``: closing it closes
    its child, the host root and the host, each once."""
    opened = open_standalone(host_config, built, **options)
    (runner,) = opened.runners
    return replace(runner, closes=opened.close)


def open_host_free() -> OwnedChild:
    """The graph an app built without a host root serves its host routes from: each host service declared absent."""
    registry = RecordingRegistry()
    throttle.register_absent(registry)
    return build_container(registry, HOST_LIFETIMES, owner=HOST_OWNER, values=ScopeValues())

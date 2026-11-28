"""The host process scope and its runner children.

Infrastructure opens before the legacy host assembler. Named legacy leaves bridge the remaining runner composition;
adopted host services depend on registered infrastructure rather than the assembled host."""

from __future__ import annotations

import contextlib
import os
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from datetime import tzinfo
from typing import Any, TypedDict, Unpack, cast

from blizzard.foundation.clock import IClock, IMonotonicClock, SystemClock
from blizzard.foundation.flags import FlagRegistry, Flags
from blizzard.foundation.platform_tracing.handle import IPlatformTracing
from blizzard.foundation.registry.interface import port
from blizzard.foundation.registry.internal.recording import RecordingRegistry
from blizzard.foundation.registry.internal.translation import HOST_LIFETIMES, build_container
from blizzard.foundation.registry.legacy import LegacyRegistry
from blizzard.foundation.registry.scopes import OwnedChild, ScopeValues, supply
from blizzard.foundation.roles import collaborator
from blizzard.foundation.trace_export.exporter import ITraceExporter
from blizzard.runner import host_legacy
from blizzard.runner import wiring as runner_wiring
from blizzard.runner.auth import wiring as auth
from blizzard.runner.composition import (
    HostProcess,
    HostRunnerSet,
    IRunnerResume,
    IWiredRunner,
    RunnerCompose,
    RunnerProcess,
    build_host_of_one,
)
from blizzard.runner.config import RunnerConfig, SubscriptionDeclaration
from blizzard.runner.events.broker import EventBroker
from blizzard.runner.harness import wiring as harness
from blizzard.runner.harness.binding_selection import BindingSelection
from blizzard.runner.harness.bundle import BundleSnapshot
from blizzard.runner.host_config import BuiltRunner, HostConfig
from blizzard.runner.host_records import wiring as host_records
from blizzard.runner.hosting import wiring as hosting
from blizzard.runner.hub import wiring as hub
from blizzard.runner.hub.host_options import HostHubOptions
from blizzard.runner.hub.host_registration import HostRegistration, HostRegistrationHolder
from blizzard.runner.lifecycle import wiring as lifecycle
from blizzard.runner.listeners import Listeners
from blizzard.runner.loop.host_tick import HostTickInForce
from blizzard.runner.process import wiring as process
from blizzard.runner.process.child_env import ToolProfileHolder
from blizzard.runner.settings import wiring as settings
from blizzard.runner.status import wiring as status
from blizzard.runner.store import host_resources
from blizzard.runner.store.wiring import HostStoreRefs, register_host_stores
from blizzard.runner.stores import HostStores
from blizzard.runner.subscriptions import wiring as subscriptions
from blizzard.runner.throttle import wiring as throttle
from blizzard.runner.throttle.usage_limits import IWriteSubscriptionUsageLimitRepository
from blizzard.runner.tracing import wiring as tracing
from blizzard.runner.tracing.host_options import HostTracingOptions
from blizzard.runner.tracing.received_export import IReceivedTelemetryExport
from blizzard.runner.tracing.receiving import TelemetryReceiver
from blizzard.runner.tracing.runner_export import RunnerTraceOptions
from blizzard.runner.transfer import wiring as transfer
from blizzard.runner.usage import wiring as usage

#: The process scope's owner id.
HOST_OWNER = "host"


def register_host_store_graph(registry: LegacyRegistry) -> HostStoreRefs:
    """Register host stores and their concept-owned ports over registered connections, clock and process probe."""
    stores = register_host_stores(registry)
    auth.register_host_store_ports(registry, stores.jti_cache)
    harness.register_host_store_ports(registry, stores.workspace_prompt)
    host_records.register_host_store_ports(registry, stores.workspaces, stores.subscriptions)
    hosting.register_host_store_ports(registry, stores.hosted_runners)
    hub.register_host_store_ports(registry, stores.host_identity, stores.hub_tokens)
    lifecycle.register_host_store_ports(registry, stores.graph_artifacts, stores.daemon_liveness)
    settings.register_host_store_ports(registry, stores.settings)
    throttle.register_host_store_ports(registry, stores.usage_limits)
    throttle.register(registry, stores.usage_limits)
    transfer.register_host_store_ports(registry, stores.host_ingests)
    usage.register_host_store_ports(registry, stores.external_usage, stores.host_lane)
    return stores


def register_host_infrastructure(registry: LegacyRegistry, *, registered: bool = False) -> HostStoreRefs:
    """Register infrastructure over borrowed config, clocks and platform tracing."""
    host_resources.register(registry)
    process.register_host_infrastructure(registry)
    subscriptions.register_host_infrastructure(registry)
    tracing.register_host_infrastructure(registry)
    hosting.register_host_infrastructure(registry)
    hub.register_host_infrastructure(registry, registered=registered)
    lifecycle.register_host_infrastructure(registry, registered=registered)
    runner_wiring.register_host_infrastructure(registry)
    host_legacy.register_runner_children(registry)
    stores = register_host_store_graph(registry)
    status.register_host_infrastructure(registry, stores.invariants)
    return stores


def open_host_infrastructure(
    config: RunnerConfig,
    clock: IClock,
    monotonic_clock: IMonotonicClock,
    runtime: runner_wiring.HostRuntimeOptions,
    hub_options: HostHubOptions,
    tracing_options: HostTracingOptions,
    *,
    tick_in_force: HostTickInForce[SubscriptionDeclaration],
    extend: Callable[[LegacyRegistry], None] | None = None,
    extra_values: ScopeValues | None = None,
) -> OwnedChild:
    """Open and eagerly acquire the host scope before assembling the legacy host.

    The caller transfers this owner to the host and closes it after all runner scopes.
    """
    registry = LegacyRegistry()
    runner_wiring.register_infrastructure_contexts(registry)
    register_host_infrastructure(registry, registered=bool(hub_options.token))
    if extend is not None:
        extend(registry)
    values = ScopeValues(
        (
            supply(port(RunnerConfig), config),
            supply(port(IClock), clock),
            supply(port(IMonotonicClock), monotonic_clock),
            supply(port(runner_wiring.HostRuntimeOptions), runtime),
            supply(port(HostHubOptions), hub_options),
            supply(port(HostTracingOptions), tracing_options),
            supply(port(HostTickInForce[SubscriptionDeclaration]), tick_in_force),
            *(extra_values.items if extra_values is not None else ()),
        )
    )
    owner = build_container(registry, HOST_LIFETIMES, owner=HOST_OWNER, values=values)
    try:
        for key in (
            HostStores,
            EventBroker | None,
            tzinfo,
            Listeners,
            ToolProfileHolder,
            FlagRegistry,
            Flags,
            HostRegistrationHolder,
            hub.HostHubTransport | None,
            hub.HttpHostHubClient | None,
            HostRegistration | None,
            lifecycle.HostLaneDrain | None,
            TelemetryReceiver,
            subscriptions.LazyUsageHttpClient,
            status.HostInvariantCheck,
            hosting.RetirementNotices,
            process.LinuxProcessProbe,
            process.IChildProcesses,
        ):
            owner.resolver.get(key)
    except BaseException as failure:
        try:
            owner.close()
        except BaseException as cleanup:
            failure.add_note(f"closing host infrastructure after opening also failed: {cleanup!r}")
        raise
    return owner


def register_host(registry: LegacyRegistry) -> None:
    """Declare the existing borrowed-host runner graph."""
    usage_limits = host_legacy.register_legacy_host(registry)
    throttle.register(registry, usage_limits)


@collaborator
@dataclass(frozen=True)
class HostRoot[W: IWiredRunner, R: IRunnerResume]:
    """The host's open process scope and the runner set that owns the runner scopes beneath it."""

    owner: OwnedChild
    runners: HostRunnerSet[W, R]
    owns_owner: bool = True

    @classmethod
    def attach(
        cls,
        owner: OwnedChild,
        host: HostProcess,
        host_config: HostConfig,
        *,
        compose: RunnerCompose | None = None,
        eager: Sequence[type[Any]] = (RunnerProcess,),
        owns_owner: bool = False,
    ) -> HostRoot[W, R]:
        """Attach the assembled host to the already-open process scope and bind its runner children."""
        try:
            reference = owner.resolver.get(host_legacy.LegacyHostRef)
            already_attached = reference.host is not None
            reference.attach(host)
            runners = cast(
                "HostRunnerSet[W, R]",
                host_legacy.legacy_runner_set(host)
                if already_attached
                else owner.resolver.get(HostRunnerSet[Any, Any]),
            )
            runner_compose = compose if compose is not None else RunnerCompose()
            runners.bind(
                owner,
                eager=eager,
                child_values=ScopeValues(
                    (
                        supply(port(HostConfig), host_config),
                        supply(port(RunnerCompose), runner_compose),
                        supply(
                            port(RunnerTraceOptions),
                            RunnerTraceOptions(
                                os.environ if runner_compose.environ is None else runner_compose.environ,
                                runner_compose.trace_exporter,
                            ),
                        ),
                    )
                ),
            )
            host.root_managed = True
            if owns_owner:
                host.infrastructure_transferred = True
        except BaseException as failure:
            if owns_owner:
                try:
                    owner.close()
                except BaseException as cleanup:
                    failure.add_note(f"closing the process scope after it also failed: {cleanup!r}")
            raise
        return cls(owner, runners, owns_owner)

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
                supply(port(IClock), host.clock),
                supply(port(IWriteSubscriptionUsageLimitRepository), host.host_stores.usage_limits),
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
        cls,
        host: HostProcess,
        host_config: HostConfig,
        *,
        compose: RunnerCompose | None = None,
        registry: LegacyRegistry | None = None,
        owns_infrastructure: bool = False,
    ) -> HostRoot[W, R]:
        """The serving root over ``host``: ``registry``, else :func:`register_host`'s graph, each runner child
        composing its runner as it opens, so a bad entry fails the child's opening."""
        if registry is None:
            return cls.attach(host.infrastructure, host, host_config, compose=compose, owns_owner=owns_infrastructure)
        return cls.open(registry, host, host_config, compose=compose, eager=(RunnerProcess,))

    def close(self) -> None:
        """Close every runner the set holds, then the process scope, attempting the second when the first fails."""
        if not self.owns_owner:
            self.runners.close_all()
            return
        try:
            self.runners.close_all()
        except BaseException as failure:
            try:
                self.owner.close()
            except BaseException as cleanup:
                failure.add_note(f"closing the process scope after it also failed: {cleanup!r}")
            raise
        else:
            self.owner.close()


@dataclass(frozen=True)
class OpenedHost:
    """A host, its owning root and runner children opened beneath it but not yet served."""

    host: HostProcess
    root: HostRoot[Any, Any]
    children: tuple[OwnedChild, ...]
    #: Each child's runner, composed as its child opened, in the children's order.
    runners: tuple[RunnerProcess, ...]
    _closing: contextlib.ExitStack = field(repr=False, compare=False)

    def close(self) -> None:
        self._closing.close()


def open_host(
    host: HostProcess,
    host_config: HostConfig,
    runners: Sequence[BuiltRunner] = (),
    *,
    compose: RunnerCompose | None = None,
    registry: LegacyRegistry | None = None,
) -> OpenedHost:
    """:meth:`HostRoot.serving` over ``host`` and a child beneath it for each of ``runners``, composed under
    ``compose`` and held among the host's runners. The opened host owns ``host``: a failure closes what opened, then
    ``host``."""
    closing = contextlib.ExitStack()
    try:
        closing.callback(host.close)
        root = HostRoot[Any, Any].serving(
            host, host_config, compose=compose, registry=registry, owns_infrastructure=True
        )
        closing.callback(root.close)
        children: list[OwnedChild] = []
        composed: list[RunnerProcess] = []
        for built in runners:
            runner, child = root.runners.open_composed(built)
            closing.callback(root.runners.close_graph, runner, child)
            children.append(child)
            composed.append(runner)
        host.runners = (*host.runners, *composed)
        return OpenedHost(host, root, tuple(children), tuple(composed), closing.pop_all())
    except BaseException as failure:
        try:
            closing.close()
        except BaseException as cleanup:
            failure.add_note(f"closing the host after opening also failed: {cleanup!r}")
        raise


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

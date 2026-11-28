"""Runner audit wiring uses borrowed engines and the caller's probe."""

from __future__ import annotations

from unittest.mock import Mock

from sqlalchemy import create_engine, event, text

from blizzard.foundation.clock import IClock, SystemClock
from blizzard.foundation.logging import get_logger
from blizzard.foundation.registry.interface import Lifetime, port
from blizzard.foundation.registry.internal.recording import RecordingRegistry
from blizzard.foundation.registry.internal.translation import HOST_LIFETIMES, build_container
from blizzard.foundation.registry.scopes import ScopeValues, supply
from blizzard.runner import stores as ports
from blizzard.runner.auth import wiring as auth
from blizzard.runner.harness import wiring as harness
from blizzard.runner.host_records import ports as host_record_ports
from blizzard.runner.host_records import wiring as host_records
from blizzard.runner.hosting import wiring as hosting
from blizzard.runner.hub import wiring as hub
from blizzard.runner.lifecycle import wiring as lifecycle
from blizzard.runner.process.internal.linux_process_probe import LinuxProcessProbe
from blizzard.runner.process.probe import IProcessProbe
from blizzard.runner.settings import wiring as settings
from blizzard.runner.store.errors import RunnerStoreErrorFactory
from blizzard.runner.store.scoped import RunnerStoreConnections
from blizzard.runner.store.wiring import build_host_stores, register_host_stores
from blizzard.runner.throttle import wiring as throttle
from blizzard.runner.transfer import wiring as transfer
from blizzard.runner.usage import wiring as usage
from blizzard.tools.invariants import runner_invariants
from tests.support.container_guards import default_factory_violations, duplicate_owners, role_key_violations


def test_runner_checker_borrows_engine_and_probe() -> None:
    engine = create_engine("sqlite://")
    disposed = Mock()
    event.listen(engine, "engine_disposed", disposed)
    probe = Mock()
    checker = runner_invariants(engine, process=probe)
    assert checker.process is probe
    assert isinstance(runner_invariants(engine).process, LinuxProcessProbe)
    disposed.assert_not_called()
    with engine.connect() as connection:
        assert connection.execute(text("select 1")).scalar_one() == 1
    engine.dispose()


def test_bundle_exposes_same_audit_and_distinct_record_roles() -> None:
    engine = create_engine("sqlite://")
    stores = build_host_stores(engine, errors=RunnerStoreErrorFactory(get_logger("test")), clock=SystemClock())
    assert stores.workspaces is not stores.subscriptions
    engine.dispose()


def test_registered_roles_resolve_to_bundle_instances_without_owning_engine() -> None:
    engine = create_engine("sqlite://")
    disposed = Mock()
    event.listen(engine, "engine_disposed", disposed)
    clock = SystemClock()
    connections = RunnerStoreConnections(engine, RunnerStoreErrorFactory(get_logger("test")))
    probe = Mock()
    registry = RecordingRegistry()
    for key in (RunnerStoreConnections, IClock, IProcessProbe):
        registry.context(port(key), lifetime=Lifetime.PROCESS)
    refs = register_host_stores(registry)
    auth.register_host_store_ports(registry, refs.jti_cache)
    harness.register_host_store_ports(registry, refs.workspace_prompt)
    lifecycle.register_host_store_ports(registry, refs.graph_artifacts, refs.daemon_liveness)
    usage.register_host_store_ports(registry, refs.external_usage, refs.host_lane)
    hub.register_host_store_ports(registry, refs.host_identity, refs.hub_tokens)
    transfer.register_host_store_ports(registry, refs.host_ingests)
    hosting.register_host_store_ports(registry, refs.hosted_runners)
    throttle.register_host_store_ports(registry, refs.usage_limits)
    settings.register_host_store_ports(registry, refs.settings)
    host_records.register_host_store_ports(registry, refs.workspaces, refs.subscriptions)
    assert duplicate_owners(registry) == []
    assert role_key_violations(registry) == []
    assert default_factory_violations(registry) == []
    owner = build_container(
        registry,
        HOST_LIFETIMES,
        owner="host",
        values=ScopeValues(
            (
                supply(port(RunnerStoreConnections), connections),
                supply(port(IClock), clock),
                supply(port(IProcessProbe), probe),
            )
        ),
    )
    try:
        bundle = owner.resolver.get(ports.HostStores)
        assert owner.resolver.get(ports.IRunnerAudit) is bundle.runner_audit
        assert owner.resolver.get(host_record_ports.IWriteHostWorkspaceRecords) is bundle.workspaces
        assert owner.resolver.get(host_record_ports.IReadHostWorkspaceRecords) is bundle.workspaces
        assert owner.resolver.get(host_record_ports.IWriteHostSubscriptionRecords) is bundle.subscriptions
        assert owner.resolver.get(host_record_ports.IReadHostSubscriptionRecords) is bundle.subscriptions
        assert bundle.workspaces is not bundle.subscriptions
        for field, role in (
            ("workspace_prompt", ports.IWriteWorkspacePromptRepository),
            ("graph_artifacts", ports.IWriteGraphArtifactRepository),
            ("jti_cache", ports.IJtiCache),
            ("daemon_liveness", ports.IWriteDaemonLivenessRepository),
            ("external_usage", ports.IWriteExternalUsageRepository),
            ("host_lane", ports.IWriteHostLaneRepository),
            ("host_identity", ports.IWriteHostIdentityRepository),
            ("host_ingests", ports.IWriteHostIngestRepository),
            ("hub_tokens", ports.IWriteHubTokenRepository),
            ("hosted_runners", ports.IWriteHostedRunnerRepository),
            ("usage_limits", ports.IWriteSubscriptionUsageLimitRepository),
            ("settings", ports.IWriteHostSettings),
        ):
            assert owner.resolver.get(role) is getattr(bundle, field)
    finally:
        owner.close()
    disposed.assert_not_called()
    engine.dispose()
    disposed.assert_called_once()

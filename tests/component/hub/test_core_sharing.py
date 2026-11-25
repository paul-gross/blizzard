"""A hub process's tenant and process scopes hold the very instances every service and the work-source registry
share (component tier) — identity, not equality, so no second copy hides behind an equal-looking one."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest

from blizzard.foundation.clock import SystemClock
from blizzard.foundation.registry.scopes import Resolver
from blizzard.foundation.store.engine import create_engine_from_url
from blizzard.hub.auth.internal.user_repository import UserRepository
from blizzard.hub.auth.users import IReadUserRepository
from blizzard.hub.domain.execution.claim import ClaimService
from blizzard.hub.domain.execution.fleet import FleetService
from blizzard.hub.domain.garden.findings.bucket import FindingBucketReader
from blizzard.hub.domain.garden.findings.model import FindingExitService, IReadFindingRepository
from blizzard.hub.domain.garden.proposals.closure import GardenProposalClosureService
from blizzard.hub.domain.observability.hub_events import IReadHubEventLog
from blizzard.hub.domain.operations.delete import DeleteService
from blizzard.hub.domain.runners.registration import IReadRunnerRegistry
from blizzard.hub.domain.urgency.ports import IReadUrgencyRepository, IWriteUrgencyRepository
from blizzard.hub.domain.urgency.services import TenantDefault, UrgencyLifecycle, UrgencyReading
from blizzard.hub.events.broker import EventBroker
from blizzard.hub.store.errors import HubStoreConnections, HubStoreError
from blizzard.hub.store.internal.finding_store import FindingStore
from blizzard.hub.store.internal.hub_event_log_store import HubEventLogStore
from blizzard.hub.store.internal.urgency_store import UrgencyStore
from blizzard.hub.store.scoped import ScopedConnections
from blizzard.hub.store.transactions import HubTransactions
from blizzard.hub.tenancy import StoreScope, TenantId
from blizzard.hub.work_sources.internal.hub_work_source import HubWorkSource
from tests.support.scaffolding import add_tenant, build_hub, configured_core, default_tenant, tenant_adapter_of
from tests.support.transition_identity import (
    port_identity_violations,
    process_identity_violations,
    process_sharing_violations,
)


@dataclass(frozen=True)
class _Planted:
    """A scope's resolver answering ``key`` with ``planted`` — a second copy the identity gates must name."""

    inner: Resolver
    key: Any
    planted: object

    def get(self, dependency_type: Any, component: str | None = "") -> Any:
        if dependency_type is self.key:
            return self.planted
        return self.inner.get(dependency_type, component)


def test_the_tenants_services_share_its_stores(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)

    exits = hub.resolve(default_tenant(hub.engine), FindingExitService)
    assert hub.default_adapter(IReadFindingRepository) is exits.repo
    assert hub.default_adapter(DeleteService) is hub.default_adapter(GardenProposalClosureService).items.delete
    assert hub.default_adapter(IReadRunnerRegistry) is hub.default_adapter(ClaimService).registry
    assert hub.default_adapter(IReadRunnerRegistry) is hub.default_adapter(FleetService).registry


def test_the_garden_proposal_closure_and_the_hub_source_share_one_edit_service(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)

    hub_editor = hub.work_sources.editor("hub")
    assert hub_editor is not None

    assert isinstance(hub_editor, HubWorkSource)
    assert hub_editor._edits is hub.default_adapter(GardenProposalClosureService).items  # pyright: ignore[reportPrivateUsage]  # the shared instance is wiring, observable only on the private seam


def test_the_harness_reads_the_default_tenants_scope(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    assert hub.app is not None

    assert tenant_adapter_of(hub.app, IReadRunnerRegistry) is hub.default_adapter(IReadRunnerRegistry)
    assert hub.app.state.hub_process is hub.process
    assert hub.events is hub.default_adapter(EventBroker)
    assert hub.default_adapter(IReadUserRepository) is hub.resolve_hub(UserRepository)


def test_a_second_tenant_gets_its_own_services_and_leaves_the_default_one_named(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    tenant = default_tenant(hub.engine)
    registry = hub.default_adapter(IReadRunnerRegistry)

    second = add_tenant(hub.engine, "second")

    assert hub.resolve(second, IReadRunnerRegistry) is not registry
    assert hub.resolve(second, EventBroker) is not hub.default_adapter(EventBroker)
    assert hub.resolve(tenant, IReadRunnerRegistry) is registry
    assert hub.client.get("/api/chunks").status_code == 200


def test_the_stores_a_process_opens_for_a_tenant_are_scoped_to_it(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    scope = StoreScope(default_tenant(hub.engine))

    process = configured_core(hub.engine, tmp_path, clock=hub.clock)

    assert process.tenants.resolve(scope, ScopedConnections).scope == scope


def test_transaction_binding_is_shared_only_within_one_tenant(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    process = configured_core(hub.engine, tmp_path, clock=hub.clock)
    first = process.tenants.open(StoreScope(default_tenant(hub.engine))).resolver
    second = process.tenants.open(StoreScope(add_tenant(hub.engine, "other"))).resolver

    assert first.get(DeleteService)._transactions is first.get(HubTransactions)  # pyright: ignore[reportPrivateUsage]  # the shared binding is wiring, observable only on the private seam
    assert second.get(HubTransactions) is not first.get(HubTransactions)


def test_the_process_reads_no_tenant_until_one_is_opened_so_a_stale_store_still_builds(tmp_path: Path) -> None:
    engine = create_engine_from_url(f"sqlite:///{tmp_path / 'empty.db'}")

    process = configured_core(engine, tmp_path, clock=SystemClock())

    with pytest.raises(HubStoreError):
        process.tenants.open(StoreScope(TenantId("ten_01J0000000000000000000000")))


def test_every_port_is_its_adapters_instance(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)

    tenant = hub.process.tenants.open(StoreScope(default_tenant(hub.engine))).resolver

    assert port_identity_violations(tenant) == []


def test_the_port_identity_gate_fails_a_planted_second_copy(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    tenant = hub.process.tenants.open(StoreScope(default_tenant(hub.engine))).resolver

    planted = _Planted(tenant, IReadFindingRepository, FindingStore(tenant.get(ScopedConnections)))

    assert port_identity_violations(planted) == ["IReadFindingRepository is not FindingStore's instance"]


def test_a_background_resolution_and_a_request_share_one_finding_service(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    tenant = default_tenant(hub.engine)
    findings = hub.default_adapter(IReadFindingRepository)

    exits = hub.process.tenants.resolve(StoreScope(tenant), FindingExitService)
    every = dict(hub.process.tenants.every_open(IReadFindingRepository))

    assert hub.resolve(tenant, IReadFindingRepository) is findings
    assert hub.resolve(tenant, FindingBucketReader) is hub.default_adapter(FindingBucketReader)
    assert every[tenant] is findings
    assert exits.repo is findings
    assert hub.resolve(add_tenant(hub.engine, "other"), IReadFindingRepository) is not findings


def test_every_process_port_is_its_container_instance(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)

    assert process_identity_violations(hub.process.resolver) == []


def test_the_process_identity_gate_fails_a_planted_second_copy(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    process = hub.process.resolver

    planted = _Planted(process, IReadHubEventLog, HubEventLogStore(process.get(HubStoreConnections)))

    assert process_identity_violations(planted) == ["IReadHubEventLog is not its container instance"]


def test_every_tenant_resolves_the_process_actions_codec_and_packaged_loaders(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    second = add_tenant(hub.engine, "second")

    for tenant in (default_tenant(hub.engine), second):
        scope = hub.process.tenants.open(StoreScope(tenant)).resolver
        assert process_sharing_violations(hub.process.resolver, scope) == []


def test_the_urgency_ports_and_services_are_one_instance_within_a_tenant_and_apart_across_two(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    first = default_tenant(hub.engine)
    second = add_tenant(hub.engine, "second")

    adapter = hub.resolve(first, UrgencyStore)
    assert hub.resolve(first, IReadUrgencyRepository) is adapter
    assert hub.resolve(first, IWriteUrgencyRepository) is adapter
    assert hub.default_adapter(UrgencyReading) is hub.resolve(first, UrgencyReading)
    for key in (UrgencyStore, UrgencyReading, TenantDefault, UrgencyLifecycle):
        assert hub.resolve(second, key) is not hub.resolve(first, key)

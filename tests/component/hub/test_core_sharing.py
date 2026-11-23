"""``build_hub_core``'s members are the very instances every service and the work-source
registry share (component tier) — identity, not equality, so no second copy hides behind
an equal-looking one."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

from blizzard.foundation.clock import SystemClock
from blizzard.foundation.store.engine import create_engine_from_url
from blizzard.hub.domain.execution.fleet import FleetService
from blizzard.hub.domain.garden.findings.bucket import FindingBucketReader
from blizzard.hub.domain.garden.findings.model import FindingExitService, IReadFindingRepository
from blizzard.hub.domain.urgency.ports import IReadUrgencyRepository, IWriteUrgencyRepository
from blizzard.hub.domain.urgency.services import TenantDefault, UrgencyLifecycle, UrgencyReading
from blizzard.hub.store.errors import HubStoreError
from blizzard.hub.store.internal.finding_store import FindingStore
from blizzard.hub.store.internal.hub_event_log_store import HubEventLogStore
from blizzard.hub.store.internal.urgency_store import UrgencyStore
from blizzard.hub.tenancy import StoreScope, TenantId
from blizzard.hub.work_sources.internal.hub_work_source import HubWorkSource
from tests.support.scaffolding import add_tenant, build_hub, configured_core, default_tenant, tenant_services_of
from tests.support.transition_identity import (
    process_identity_violations,
    process_sharing_violations,
    transition_identity_violations,
)


def test_services_share_the_cores_stores(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    services = hub.services

    exits = hub.resolve(default_tenant(hub.engine), FindingExitService)
    assert services.findings is exits.repo
    assert services.garden_proposals is services.garden_proposal_authoring.proposals
    assert services.garden_proposal_closures is services.garden_proposal_authoring.closures
    assert services.delete is services.garden_proposal_closure.items.delete
    assert services.registry is services.claim.registry
    assert services.registry is hub.default_adapter(FleetService).registry


def test_the_garden_proposal_closure_and_the_hub_source_share_one_edit_service(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)

    hub_editor = hub.work_sources.editor("hub")
    assert hub_editor is not None

    assert isinstance(hub_editor, HubWorkSource)
    assert hub_editor._edits is hub.services.garden_proposal_closure.items  # pyright: ignore[reportPrivateUsage]  # the shared instance is wiring, observable only on the private seam


def test_the_harness_serves_the_default_tenants_cached_services(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    assert hub.app is not None

    assert hub.open_services(default_tenant(hub.engine)) is hub.services
    assert tenant_services_of(hub.app) is hub.services
    assert hub.hub_services is hub.app.state.services
    assert hub.events is hub.services.events
    assert hub.services.users is hub.hub_services.users


def test_a_second_tenant_gets_its_own_services_and_leaves_the_default_one_named(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    tenant = default_tenant(hub.engine)

    second = add_tenant(hub.engine, "second")

    theirs = hub.open_services(second)
    assert theirs is not hub.services
    assert theirs.registry is not hub.services.registry
    assert theirs.events is not hub.services.events
    assert hub.open_services(tenant) is hub.services
    assert hub.client.get("/api/chunks").status_code == 200


def test_the_stores_the_core_opens_for_a_tenant_are_scoped_to_it(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    scope = StoreScope(default_tenant(hub.engine))

    core = configured_core(hub.engine, tmp_path, clock=hub.clock)

    assert core.open(scope).connections.scope == scope


def test_transaction_binding_is_shared_only_within_one_tenant(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    core = configured_core(hub.engine, tmp_path, clock=hub.clock)
    first = core.open(StoreScope(default_tenant(hub.engine)))
    second = core.open(StoreScope(add_tenant(hub.engine, "other")))

    assert first.delete._transactions is first.transactions  # pyright: ignore[reportPrivateUsage]  # the shared binding is wiring, observable only on the private seam
    assert second.transactions is not first.transactions


def test_the_core_reads_no_tenant_until_one_is_opened_so_a_stale_store_still_builds(tmp_path: Path) -> None:
    engine = create_engine_from_url(f"sqlite:///{tmp_path / 'empty.db'}")

    core = configured_core(engine, tmp_path, clock=SystemClock())

    with pytest.raises(HubStoreError):
        core.open(StoreScope(TenantId("ten_01J0000000000000000000000")))


def test_every_retained_bag_field_is_its_container_instance(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    tenant = default_tenant(hub.engine)

    stores = hub.open_stores(tenant)

    assert transition_identity_violations(stores, hub.open_services(tenant)) == []


def test_the_transition_identity_gate_fails_a_planted_second_copy(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    tenant = default_tenant(hub.engine)
    stores = hub.open_stores(tenant)
    services = hub.open_services(tenant)

    planted = replace(stores, finding_store=FindingStore(stores.connections))
    replanted = replace(services, finding_bucket=FindingBucketReader(services.findings))

    assert transition_identity_violations(planted, replanted) == [
        "TenantStores.finding_store is not its container instance",
        "TenantServices.finding_bucket is not its container instance",
    ]


def test_a_background_resolution_and_the_retained_bag_share_one_finding_service(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    tenant = default_tenant(hub.engine)
    services = hub.open_services(tenant)

    exits = hub.hub_services.process.tenants.resolve(StoreScope(tenant), FindingExitService)
    every = dict(hub.hub_services.process.tenants.every_open(IReadFindingRepository))

    assert hub.resolve(tenant, IReadFindingRepository) is services.findings
    assert hub.resolve(tenant, FindingBucketReader) is services.finding_bucket
    assert every[tenant] is services.findings
    assert exits.repo is services.findings
    assert hub.open_services(add_tenant(hub.engine, "other")).findings is not services.findings


def test_every_retained_process_field_is_its_container_instance(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    assert hub.core is not None

    assert process_identity_violations(hub.core, hub.hub_services) == []


def test_the_process_identity_gate_fails_a_planted_second_copy(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    assert hub.core is not None

    planted = replace(hub.core, hub_events=HubEventLogStore(hub.core.store_connections))

    assert process_identity_violations(planted, hub.hub_services) == [
        "HubCore.hub_events is not its container instance"
    ]


def test_every_tenant_resolves_the_process_actions_codec_and_packaged_loaders(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    assert hub.core is not None
    second = add_tenant(hub.engine, "second")

    for tenant in (default_tenant(hub.engine), second):
        assert process_sharing_violations(hub.core, hub.open_stores(tenant)) == []


def test_the_urgency_ports_and_services_are_one_instance_within_a_tenant_and_apart_across_two(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    first = default_tenant(hub.engine)
    second = add_tenant(hub.engine, "second")

    adapter = hub.resolve(first, UrgencyStore)
    assert hub.resolve(first, IReadUrgencyRepository) is adapter
    assert hub.resolve(first, IWriteUrgencyRepository) is adapter
    assert hub.services.urgency_reading is hub.resolve(first, UrgencyReading)
    for key in (UrgencyStore, UrgencyReading, TenantDefault, UrgencyLifecycle):
        assert hub.resolve(second, key) is not hub.resolve(first, key)

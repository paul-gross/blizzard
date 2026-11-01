"""``build_hub_core``'s members are the very instances every service and the work-source
registry share (component tier) — identity, not equality, so no second copy hides behind
an equal-looking one."""

from __future__ import annotations

from pathlib import Path

import pytest

from blizzard.foundation.clock import SystemClock
from blizzard.foundation.store.engine import create_engine_from_url
from blizzard.hub.composition import build_hub_core
from blizzard.hub.store.errors import HubStoreError
from blizzard.hub.tenancy import StoreScope, TenantId
from tests.support import add_tenant, build_hub, default_tenant, tenant_services_of

pytestmark = pytest.mark.component


def test_services_share_the_cores_stores(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    services = hub.services

    assert services.findings is services.finding_exit._repo
    assert services.garden_proposals is services.garden_proposal_authoring._proposals
    assert services.garden_proposal_closures is services.garden_proposal_authoring._closures
    assert services.delete is services.garden_proposal_closure._items._delete  # type: ignore[attr-defined]
    assert services.registry is services.claim._registry
    assert services.registry is services.fleet._registry
    assert services.registry is services.claim._exclusive._registry  # type: ignore[attr-defined]


def test_the_garden_proposal_closure_and_the_hub_source_share_one_edit_service(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)

    hub_editor = hub.work_sources.editor("hub")
    assert hub_editor is not None

    assert hub_editor._edits is hub.services.garden_proposal_closure._items  # type: ignore[attr-defined]


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

    core = build_hub_core(hub.engine, clock=hub.clock)

    assert core.open(scope).connections.scope == scope


def test_transaction_binding_is_shared_only_within_one_tenant(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    core = build_hub_core(hub.engine, clock=hub.clock)
    first = core.open(StoreScope(default_tenant(hub.engine)))
    second = core.open(StoreScope(add_tenant(hub.engine, "other")))

    assert first.delete._transactions is first.transactions
    assert first.chunk_stores.exclusive._transactions is first.transactions  # type: ignore[attr-defined]
    assert second.transactions is not first.transactions


def test_the_core_reads_no_tenant_until_one_is_opened_so_a_stale_store_still_builds(tmp_path: Path) -> None:
    engine = create_engine_from_url(f"sqlite:///{tmp_path / 'empty.db'}")

    core = build_hub_core(engine, clock=SystemClock())

    with pytest.raises(HubStoreError):
        core.open(StoreScope(TenantId("ten_01J0000000000000000000000")))

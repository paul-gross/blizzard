"""The flow executor as the hub container holds it: the hosted driver's accessors resolve the very instances a request
beneath the tenant resolves, no tenant-owned instance is shared across tenants, and a tick over two tenants writes each
tenant's turn in that tenant alone (component tier)."""

from __future__ import annotations

from pathlib import Path

from blizzard.foundation.registry.scopes import ScopeValues
from blizzard.hub.api.chunk_events import AnnouncedTurns
from blizzard.hub.delivery.flow_turn import TurnRunner
from blizzard.hub.domain.delivery.ports.flows import IReadFlows, IWriteFlows
from blizzard.hub.domain.delivery.reconcile import FlowLineReconciler
from blizzard.hub.forge.budget_notices import BudgetNoticeDrain
from blizzard.hub.open_tenants import OpenTenants
from blizzard.hub.store.internal.flow_store import FlowStore
from blizzard.hub.tenancy import StoreScope, TenantId
from tests.support.flow_driver_world import World
from tests.support.scaffolding import TENANT_HEADER, HubHarness, add_tenant, build_hub, default_tenant

# The services hold what they were built over privately; reading it is the only way to see it is shared.
# pyright: reportPrivateUsage=false


def _held(hub: HubHarness, tenant: StoreScope) -> dict[str, object]:
    """What the driver's accessors hand back for ``tenant``, each by its role, beside the same type's request-scope
    resolution."""
    tenants = hub.process.tenants
    return {
        "reconciler": tenants.resolve(tenant, FlowLineReconciler),
        "announced turns": tenants.resolve(tenant, AnnouncedTurns),
        "turns": tenants.resolve(tenant, TurnRunner),
        "notices": tenants.resolve(tenant, BudgetNoticeDrain),
        "flow store": tenants.resolve(tenant, FlowStore),
        "flow reads": tenants.resolve(tenant, IReadFlows),
        "flow writes": tenants.resolve(tenant, IWriteFlows),
    }


def test_the_drivers_accessors_hold_the_instances_a_request_beneath_the_tenant_resolves(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    tenant = StoreScope(default_tenant(hub.engine))
    held = _held(hub, tenant)

    request = hub.process.tenants.request(tenant, ScopeValues())
    try:
        resolved = {
            "reconciler": request.resolver.get(FlowLineReconciler),
            "announced turns": request.resolver.get(AnnouncedTurns),
            "turns": request.resolver.get(TurnRunner),
            "notices": request.resolver.get(BudgetNoticeDrain),
            "flow store": request.resolver.get(FlowStore),
        }
    finally:
        request.close()

    for name, instance in resolved.items():
        assert instance is held[name], name
    announced = held["announced turns"]
    assert isinstance(announced, AnnouncedTurns)
    assert announced.turns is held["turns"]
    turns, reconciler = held["turns"], held["reconciler"]
    assert isinstance(turns, TurnRunner) and isinstance(reconciler, FlowLineReconciler)
    assert turns._flows is held["flow store"] is reconciler._flows
    assert held["flow reads"] is held["flow store"] is held["flow writes"]


def test_no_tenant_owned_flow_instance_is_shared_across_tenants(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    first = _held(hub, StoreScope(default_tenant(hub.engine)))
    second_tenant = StoreScope(add_tenant(hub.engine, "second"))
    hub.resolve_hub(OpenTenants).refresh()
    second = _held(hub, second_tenant)

    assert [name for name, instance in first.items() if instance is second[name]] == []


def _turns_in(hub: HubHarness, tenant: TenantId, chunk_id: str) -> int:
    """How many turns ``tenant``'s own flow reads hold for ``chunk_id``'s submissions."""
    flows = hub.resolve(tenant, IReadFlows)
    return sum(
        len(flows.turns_page(each.submission_id, before=None, limit=50)) for each in flows.submissions_of(chunk_id)
    )


def test_a_tick_over_two_tenants_reaches_each_through_its_own_scope_and_writes_in_the_row_tenant_alone(
    tmp_path: Path,
) -> None:
    world = World(tmp_path)
    other = world.hub.add_tenant("other")
    world.hub.client.headers[TENANT_HEADER] = world.tenant
    chunk_id = world.at_merge(ref="first")

    result = world.tick()

    assert set(result.reconciled) == {world.tenant, other.tenant}
    assert {started.tenant for started in result.started} == {world.tenant}
    assert _turns_in(world.hub, world.tenant, chunk_id) == 1
    assert _turns_in(world.hub, other.tenant, chunk_id) == 0

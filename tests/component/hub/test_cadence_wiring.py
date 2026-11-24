"""The cadence registrations as the hub container holds them: within a tenant each adapter, port and service is one
instance, shared by every holder; across tenants the instances differ; and the sweep's tenant half is the tenant
container's own (component tier)."""

from __future__ import annotations

from pathlib import Path

from blizzard.hub.domain.cadence.mint import CadenceMint, ErrandPlanning
from blizzard.hub.domain.cadence.ports import IReadCadenceRepository, IReadErrandRunsRepository, IWriteCadenceRepository
from blizzard.hub.domain.cadence.services import (
    CadenceAttention,
    CadenceAuthoring,
    CadenceLifecycle,
    CadenceReading,
    CadenceUrgencyClearing,
)
from blizzard.hub.domain.cadence.sweep import CadenceSweep, MintAnnouncement, TenantCadence
from blizzard.hub.store.internal.cadence_store import CadenceStore
from blizzard.hub.store.internal.errand_runs_store import ErrandRunsStore
from blizzard.hub.tenancy import StoreScope
from tests.support.scaffolding import HubHarness, add_tenant, build_hub, default_tenant

# The services hold what they were built over privately; reading it is the only way to see it is shared.
# pyright: reportPrivateUsage=false


def _shared(hub: HubHarness, tenant: StoreScope) -> dict[str, list[object]]:
    """Every holder of each cadence instance in ``tenant``, by what the instance is."""
    scope = hub.hub_services.process.tenants.open(tenant).resolver
    attention = scope.get(CadenceAttention)
    cadence = scope.get(TenantCadence)
    return {
        "cadences": [
            scope.get(CadenceStore),
            scope.get(IReadCadenceRepository),
            scope.get(IWriteCadenceRepository),
            scope.get(CadenceReading)._cadences,
            scope.get(CadenceAuthoring)._cadences,
            scope.get(CadenceLifecycle)._cadences,
            scope.get(CadenceUrgencyClearing)._cadences,
            cadence._cadences,
        ],
        "runs": [
            scope.get(ErrandRunsStore),
            scope.get(IReadErrandRunsRepository),
            scope.get(CadenceReading)._runs,
            scope.get(CadenceMint)._runs,
            cadence._runs,
        ],
        "attention": [attention, scope.get(CadenceReading)._attention, cadence._attention],
        "planning": [scope.get(ErrandPlanning), cadence._planning],
        "mint": [scope.get(CadenceMint), cadence._mint],
        "announcement": [scope.get(MintAnnouncement), cadence._announce],
        "tenant cadence": [cadence, scope.get(TenantCadence)],
    }


def _hub_with_two_tenants(tmp_path: Path) -> tuple[HubHarness, StoreScope, StoreScope]:
    hub = build_hub(tmp_path)
    first = StoreScope(default_tenant(hub.engine))
    second = StoreScope(add_tenant(hub.engine, "second"))
    hub.hub_services.tenants.open_tenants.refresh()
    return hub, first, second


def test_each_cadence_key_is_one_instance_within_a_tenant(tmp_path: Path) -> None:
    hub, first, second = _hub_with_two_tenants(tmp_path)

    for tenant in (first, second):
        for name, held in _shared(hub, tenant).items():
            assert all(each is held[0] for each in held), name


def test_the_cadence_instances_differ_across_tenants(tmp_path: Path) -> None:
    hub, first, second = _hub_with_two_tenants(tmp_path)

    mine, theirs = _shared(hub, first), _shared(hub, second)

    assert [name for name, held in mine.items() if held[0] is theirs[name][0]] == []


def test_the_sweeps_tenant_half_is_the_tenant_containers_own_and_the_routes_hold_the_same_inputs(
    tmp_path: Path,
) -> None:
    hub = build_hub(tmp_path)
    tenant = StoreScope(default_tenant(hub.engine))
    sweep = hub.hub_services.cadence
    assert isinstance(sweep, CadenceSweep)

    swept = sweep._tenant(tenant.tenant)

    scope = hub.hub_services.process.tenants.open(tenant).resolver
    assert swept is scope.get(TenantCadence)
    assert swept._attention is scope.get(CadenceReading)._attention
    assert swept._planning is hub.services.errand_planning
    assert swept._mint is hub.services.cadence_mint

"""The garden run-read registrations as the hub container holds them: within a tenant each adapter, port and service
is one instance, shared by every holder; across tenants the instances differ (component tier)."""

from __future__ import annotations

from pathlib import Path

from blizzard.hub.domain.garden.findings.model import IReadFindingSetRepository
from blizzard.hub.domain.garden.findings.trend import GardenTrendService, IReadGardenTrendRepository
from blizzard.hub.domain.garden.formats import IGardenFormats
from blizzard.hub.domain.garden.routines import IReadRoutineScopeRepository
from blizzard.hub.domain.garden.runs.history import GardenRunService, IReadGardenRunRepository
from blizzard.hub.domain.garden.runs.run import RunService
from blizzard.hub.domain.garden.runs.sweeps import GardenSweepsService, IReadGardenSweepsRepository
from blizzard.hub.store.internal.garden_run_store import GardenRunStore
from blizzard.hub.store.internal.garden_sweeps_store import GardenSweepsStore
from blizzard.hub.store.internal.garden_trend_store import GardenTrendStore
from blizzard.hub.tenancy import StoreScope
from tests.support.scaffolding import HubHarness, add_tenant, build_hub, default_tenant

# The services hold what they were built over privately; reading it is the only way to see it is shared.
# pyright: reportPrivateUsage=false


def _shared(hub: HubHarness, tenant: StoreScope) -> dict[str, list[object]]:
    """Every holder of each garden run-read instance in ``tenant``, by what the instance is."""
    scope = hub.process.tenants.open(tenant).resolver
    return {
        "trends": [
            scope.get(GardenTrendStore),
            scope.get(IReadGardenTrendRepository),
            scope.get(GardenTrendService)._repo,
        ],
        "sweeps": [
            scope.get(GardenSweepsStore),
            scope.get(IReadGardenSweepsRepository),
            scope.get(GardenSweepsService)._repo,
        ],
        "runs": [scope.get(GardenRunStore), scope.get(IReadGardenRunRepository), scope.get(GardenRunService)._repo],
        "formats": [scope.get(IGardenFormats), scope.get(GardenRunService)._formats],
        "finding sets": [scope.get(IReadFindingSetRepository), scope.get(RunService)._finding_sets],
        "routine scopes": [scope.get(IReadRoutineScopeRepository), scope.get(RunService)._routine_scopes],
    }


def _hub_with_two_tenants(tmp_path: Path) -> tuple[HubHarness, StoreScope, StoreScope]:
    hub = build_hub(tmp_path)
    first = StoreScope(default_tenant(hub.engine))
    second = StoreScope(add_tenant(hub.engine, "second"))
    hub.hub_services.tenants.open_tenants.refresh()
    return hub, first, second


def test_each_garden_run_key_is_one_instance_within_a_tenant(tmp_path: Path) -> None:
    hub, first, second = _hub_with_two_tenants(tmp_path)

    for tenant in (first, second):
        for name, held in _shared(hub, tenant).items():
            assert all(each is held[0] for each in held), name


def test_the_garden_run_instances_differ_across_tenants_but_for_the_stateless_formats(tmp_path: Path) -> None:
    hub, first, second = _hub_with_two_tenants(tmp_path)

    shared_across = {name for name, (mine, theirs) in _pairs(hub, first, second) if mine is theirs}

    assert shared_across == {"formats"}


def _pairs(hub: HubHarness, first: StoreScope, second: StoreScope) -> list[tuple[str, tuple[object, object]]]:
    mine, theirs = _shared(hub, first), _shared(hub, second)
    return [(name, (held[0], theirs[name][0])) for name, held in mine.items()]

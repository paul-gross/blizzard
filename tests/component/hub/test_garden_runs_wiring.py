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
    scope = hub.hub_services.process.tenants.open(tenant).resolver
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


def test_the_garden_run_keys_share_identity_within_a_tenant_and_differ_across_tenants(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    first = StoreScope(default_tenant(hub.engine))
    second = StoreScope(add_tenant(hub.engine, "second"))
    hub.hub_services.tenants.open_tenants.refresh()

    mine, theirs = _shared(hub, first), _shared(hub, second)

    for name, held in (*mine.items(), *theirs.items()):
        assert all(each is held[0] for each in held), name
    for (name, held), (_, other) in zip(mine.items(), theirs.items(), strict=True):
        if name == "formats":
            assert held[0] is other[0], name
        else:
            assert held[0] is not other[0], name

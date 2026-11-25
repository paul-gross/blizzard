"""The garden authoring registrations as the hub container holds them: within a tenant each store, port, formats
instance and service is one instance, shared by every port alias and the services built over it; across tenants the
instances differ (component tier)."""

from __future__ import annotations

from pathlib import Path

from blizzard.hub.api.garden_formats import GardenFormats
from blizzard.hub.domain.garden.delivery.stage_check import GardenStageCheck
from blizzard.hub.domain.garden.findings.model import (
    FindingExitService,
    IFindingExitResolver,
    IReadFindingSetRepository,
    IWriteFindingSetRepository,
)
from blizzard.hub.domain.garden.formats import IGardenFormats
from blizzard.hub.domain.garden.proposals.closure import (
    GardenProposalClosureService,
    IReadGardenProposalClosureRepository,
    IWriteGardenProposalClosureRepository,
)
from blizzard.hub.domain.garden.proposals.model import (
    GardenProposalAuthoring,
    IReadGardenProposalRepository,
    IWriteGardenProposalRepository,
    RoutineGardenProposalReader,
)
from blizzard.hub.domain.garden.proposals.resolution import AnsweredFindingsReader, GardenProposalDeliveryResolution
from blizzard.hub.domain.garden.routines import (
    IReadRoutineRepository,
    IReadRoutineScopeRepository,
    IWriteRoutineRepository,
    RoutineAuthoring,
    RoutineLifecycle,
    RoutineScopeMembership,
)
from blizzard.hub.domain.garden.run_context import IReadRunContextRepository, IWriteRunContextRepository
from blizzard.hub.domain.garden.runs.baselines import RoutineBaselineService
from blizzard.hub.domain.garden.scopes import IReadScopeRepository, IWriteScopeRepository, ScopeLifecycle, ScopeRegistry
from blizzard.hub.store.internal.finding_store import FindingSetStore
from blizzard.hub.store.internal.garden_proposal_closure_store import GardenProposalClosureStore
from blizzard.hub.store.internal.garden_proposal_store import GardenProposalStore
from blizzard.hub.store.internal.routine_scope_store import RoutineScopeStore
from blizzard.hub.store.internal.routine_store import RoutineStore
from blizzard.hub.store.internal.run_context_store import RunContextStore
from blizzard.hub.store.internal.scope_store import ScopeStore
from blizzard.hub.tenancy import StoreScope
from tests.support.scaffolding import HubHarness, add_tenant, build_hub, default_tenant

# The services hold what they were built over privately; reading it is the only way to see it is shared.
# pyright: reportPrivateUsage=false


def _shared(hub: HubHarness, tenant: StoreScope) -> dict[str, list[object]]:
    """Every holder of each garden authoring instance in ``tenant``, by what the instance is."""
    scope = hub.process.tenants.open(tenant).resolver
    return {
        "scopes": [
            scope.get(ScopeStore),
            scope.get(IReadScopeRepository),
            scope.get(IWriteScopeRepository),
            scope.get(ScopeRegistry)._scopes,
            scope.get(ScopeLifecycle)._scopes,
            scope.get(RoutineAuthoring)._scopes,
        ],
        "routines": [
            scope.get(RoutineStore),
            scope.get(IReadRoutineRepository),
            scope.get(IWriteRoutineRepository),
            scope.get(RoutineAuthoring)._routines,
            scope.get(RoutineLifecycle)._routines,
            scope.get(RoutineScopeMembership)._routines,
        ],
        "routine scopes": [
            scope.get(RoutineScopeStore),
            scope.get(IReadRoutineScopeRepository),
            scope.get(RoutineScopeMembership)._routine_scopes,
        ],
        "proposals": [
            scope.get(GardenProposalStore),
            scope.get(IReadGardenProposalRepository),
            scope.get(IWriteGardenProposalRepository),
            scope.get(GardenProposalAuthoring)._proposals,
            scope.get(RoutineGardenProposalReader)._proposals,
            scope.get(AnsweredFindingsReader)._proposals,
            scope.get(GardenProposalDeliveryResolution)._proposals,
        ],
        "closures": [
            scope.get(GardenProposalClosureStore),
            scope.get(IReadGardenProposalClosureRepository),
            scope.get(IWriteGardenProposalClosureRepository),
            scope.get(GardenProposalAuthoring)._closures,
            scope.get(GardenProposalClosureService)._closures,
            scope.get(RoutineGardenProposalReader)._closures,
            scope.get(AnsweredFindingsReader)._closures,
            scope.get(GardenProposalDeliveryResolution)._closures,
        ],
        "finding sets": [
            scope.get(FindingSetStore),
            scope.get(IReadFindingSetRepository),
            scope.get(IWriteFindingSetRepository),
            scope.get(RoutineBaselineService)._finding_sets,
        ],
        "run contexts": [
            scope.get(RunContextStore),
            scope.get(IReadRunContextRepository),
            scope.get(IWriteRunContextRepository),
        ],
        "formats": [scope.get(GardenFormats), scope.get(IGardenFormats), scope.get(GardenStageCheck)._formats],
        "finding exits": [
            scope.get(FindingExitService),
            scope.get(IFindingExitResolver),
            scope.get(GardenProposalDeliveryResolution)._exits,
        ],
        "scope registry": [scope.get(ScopeRegistry), scope.get(RoutineAuthoring)._scope_registry],
    }


def test_the_garden_authoring_keys_share_identity_within_a_tenant_and_differ_across_tenants(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    first = StoreScope(default_tenant(hub.engine))
    second = StoreScope(add_tenant(hub.engine, "second"))
    hub.hub_services.tenants.open_tenants.refresh()

    mine, theirs = _shared(hub, first), _shared(hub, second)

    for name, held in (*mine.items(), *theirs.items()):
        assert all(each is held[0] for each in held), name
    for (name, held), (_, other) in zip(mine.items(), theirs.items(), strict=True):
        if name == "formats":
            # The formats are stateless, registered once per process and shared by every tenant.
            assert held[0] is other[0], name
        else:
            assert held[0] is not other[0], name

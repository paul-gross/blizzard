"""The garden area's registrations: scope and routine authoring, baselines, proposals and their closure and delivery
resolution, the run context, the stage check and the delivery and review-findings materialization, over the adapters
it is handed.

The read and write ports of each adapter alias that adapter's one instance, so a reader and a writer in one tenant
share it; the area names no adapter. The artifact formats are handed in as the API edge's instance, and the default
graph a closure mints against is a key the API edge provides. The store-free graph declares each registration absent,
from the same rosters."""

from __future__ import annotations

from typing import Any, cast

from blizzard.foundation.clock import IClock
from blizzard.foundation.registry.interface import Lifetime, Ref, Registry, port
from blizzard.hub.domain.garden.delivery.materialize import (
    GardenDelivery,
    IReadGardenDeliveryRepository,
    IWriteGardenDeliveryRepository,
)
from blizzard.hub.domain.garden.delivery.service import GardenDeliveryRecorder
from blizzard.hub.domain.garden.delivery.stage_check import GardenStageCheck
from blizzard.hub.domain.garden.findings.model import IReadFindingSetRepository, IWriteFindingSetRepository
from blizzard.hub.domain.garden.formats import IGardenFormats
from blizzard.hub.domain.garden.proposals.closure import (
    DefaultGraphEnsurer,
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
from blizzard.hub.domain.garden.review.materialize import (
    IReadReviewFindingsRepository,
    IWriteReviewFindingsRepository,
    ReviewFindingsMaterialize,
)
from blizzard.hub.domain.garden.review.service import ReviewFindingsRecorder
from blizzard.hub.domain.garden.routines import (
    IReadRoutineRepository,
    IReadRoutineScopeRepository,
    IWriteRoutineRepository,
    RoutineAuthoring,
    RoutineLifecycle,
    RoutineScopeMembership,
    RoutineUrgencyClearing,
)
from blizzard.hub.domain.garden.run_context import IReadRunContextRepository, IWriteRunContextRepository
from blizzard.hub.domain.garden.runs.baselines import RoutineBaselineService
from blizzard.hub.domain.garden.scopes import IReadScopeRepository, IWriteScopeRepository, ScopeLifecycle, ScopeRegistry
from blizzard.hub.domain.kernel.transactions import IHubTransactions
from blizzard.hub.tenancy import STORE_UNAVAILABLE


def garden_proposal_authoring(
    proposals: IWriteGardenProposalRepository,
    closures: IReadGardenProposalClosureRepository,
    clock: IClock,
    transactions: IHubTransactions,
) -> GardenProposalAuthoring:
    """The tenant's proposal authoring. A factory because the proposal model names the closure port only under type
    checking, to stay clear of the closure module that imports it."""
    return GardenProposalAuthoring(proposals=proposals, closures=closures, clock=clock, transactions=transactions)


def routine_garden_proposals(
    proposals: IReadGardenProposalRepository, closures: IReadGardenProposalClosureRepository
) -> RoutineGardenProposalReader:
    """A routine's open proposals reader, a factory for the reason :func:`garden_proposal_authoring` is."""
    return RoutineGardenProposalReader(proposals=proposals, closures=closures)


#: The area's services registered by class.
_SERVICES: tuple[type[Any], ...] = (
    ScopeRegistry,
    ScopeLifecycle,
    RoutineScopeMembership,
    RoutineAuthoring,
    RoutineLifecycle,
    RoutineUrgencyClearing,
    RoutineBaselineService,
    AnsweredFindingsReader,
    GardenProposalDeliveryResolution,
    GardenProposalClosureService,
    GardenStageCheck,
    GardenDelivery,
    GardenDeliveryRecorder,
    ReviewFindingsMaterialize,
    ReviewFindingsRecorder,
)

#: The ports the area aliases onto the adapters it is handed, each family's read half then its write half.
_PORTS: tuple[type[Any], ...] = (
    IReadScopeRepository,
    IWriteScopeRepository,
    IReadRoutineRepository,
    IWriteRoutineRepository,
    IReadRoutineScopeRepository,
    IReadRunContextRepository,
    IWriteRunContextRepository,
    IReadGardenProposalRepository,
    IWriteGardenProposalRepository,
    IReadGardenProposalClosureRepository,
    IWriteGardenProposalClosureRepository,
    IReadFindingSetRepository,
    IWriteFindingSetRepository,
    IReadGardenDeliveryRepository,
    IWriteGardenDeliveryRepository,
    IReadReviewFindingsRepository,
    IWriteReviewFindingsRepository,
    IGardenFormats,
)


def register(
    registry: Registry,
    *,
    scopes: Ref[IWriteScopeRepository],
    routines: Ref[IWriteRoutineRepository],
    routine_scopes: Ref[IReadRoutineScopeRepository],
    run_contexts: Ref[IWriteRunContextRepository],
    proposals: Ref[IWriteGardenProposalRepository],
    closures: Ref[IWriteGardenProposalClosureRepository],
    finding_sets: Ref[IWriteFindingSetRepository],
    deliveries: Ref[IWriteGardenDeliveryRepository],
    review_findings: Ref[IWriteReviewFindingsRepository],
    formats: Ref[IGardenFormats],
) -> None:
    """Declare the area's ports over the adapters handed in, and its services, each once per tenant."""
    registry.alias(port(IReadScopeRepository), scopes)
    registry.alias(port(IWriteScopeRepository), scopes)
    registry.alias(port(IReadRoutineRepository), routines)
    registry.alias(port(IWriteRoutineRepository), routines)
    registry.alias(port(IReadRoutineScopeRepository), routine_scopes)
    registry.alias(port(IReadRunContextRepository), run_contexts)
    registry.alias(port(IWriteRunContextRepository), run_contexts)
    registry.alias(port(IReadGardenProposalRepository), proposals)
    registry.alias(port(IWriteGardenProposalRepository), proposals)
    registry.alias(port(IReadGardenProposalClosureRepository), closures)
    registry.alias(port(IWriteGardenProposalClosureRepository), closures)
    registry.alias(port(IReadFindingSetRepository), finding_sets)
    registry.alias(port(IWriteFindingSetRepository), finding_sets)
    registry.alias(port(IReadGardenDeliveryRepository), deliveries)
    registry.alias(port(IWriteGardenDeliveryRepository), deliveries)
    registry.alias(port(IReadReviewFindingsRepository), review_findings)
    registry.alias(port(IWriteReviewFindingsRepository), review_findings)
    registry.alias(port(IGardenFormats), formats)
    for service in _SERVICES:
        registry.service(service, lifetime=Lifetime.TENANT)
    registry.factory(garden_proposal_authoring, lifetime=Lifetime.TENANT)
    registry.factory(routine_garden_proposals, lifetime=Lifetime.TENANT)


def register_absent(registry: Registry) -> None:
    """Declare the area's ports, services and the default-graph key absent, for the store-free graph."""
    for missing in (*_PORTS, *_SERVICES, GardenProposalAuthoring, RoutineGardenProposalReader):
        registry.absent(port(missing), lifetime=Lifetime.TENANT, label=STORE_UNAVAILABLE)
    # A callable alias keys the ensurer, so it is named as its key rather than as a class.
    registry.absent(port(cast("type[Any]", DefaultGraphEnsurer)), lifetime=Lifetime.TENANT, label=STORE_UNAVAILABLE)

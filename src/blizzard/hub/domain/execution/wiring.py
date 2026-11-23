"""The execution area's registrations: the detach path, the fleet and host-retirement services, the host's own fact
lane, the served-project reads a runner's declaration resolves against, the version window a registration is judged by,
and the chunk execution services — claim, apply, decision, requeue, release, question and fact ingest.

The services resolve the runners area's ports and the chunk reads by key; the area names no adapter. The version window
is a process value a root supplies, aliased onto the port the fleet judges through; a root without one declares it
absent. The store-free graph declares each registration absent, from the same roster."""

from __future__ import annotations

from typing import Any

from blizzard.foundation.clock import IClock
from blizzard.foundation.registry.interface import Lifetime, Ref, Registry, port
from blizzard.hub.domain.chunk.event_log import EventLogService
from blizzard.hub.domain.chunk.model import WorkRefLabel
from blizzard.hub.domain.chunk.ports.artifacts import IReadChunkArtifactsRepository
from blizzard.hub.domain.chunk.ports.decisions import IWriteChunkDecisionsRepository
from blizzard.hub.domain.chunk.ports.escalations import IWriteChunkEscalationsRepository
from blizzard.hub.domain.chunk.ports.facts import IReadChunkFactsRepository
from blizzard.hub.domain.chunk.ports.movement import IWriteChunkMovementRepository
from blizzard.hub.domain.chunk.ports.record import IReadChunkRecordRepository
from blizzard.hub.domain.chunk.ports.route import IReadChunkRouteRepository, IWriteChunkRouteRepository
from blizzard.hub.domain.chunk.ports.work_sources import IWorkClosers
from blizzard.hub.domain.execution.apply import ApplyService, IHubStepWake
from blizzard.hub.domain.execution.claim import ClaimService
from blizzard.hub.domain.execution.decisions import DecisionService, RequeueService
from blizzard.hub.domain.execution.detach import DetachService
from blizzard.hub.domain.execution.facts import FactIngestService
from blizzard.hub.domain.execution.fleet import FleetService
from blizzard.hub.domain.execution.host_facts import HostFactIngestService
from blizzard.hub.domain.execution.host_retirement import HostRetirementService
from blizzard.hub.domain.execution.questions import QuestionService
from blizzard.hub.domain.execution.release import ReleaseService
from blizzard.hub.domain.kernel.transactions import IHubTransactions
from blizzard.hub.domain.projects.bulk_resolve import BulkProjectResolver
from blizzard.hub.domain.runners.host import IReadHostRegistry
from blizzard.hub.domain.runners.registration import IWriteRunnerRegistry, RetiredRunnerGuard
from blizzard.hub.domain.runners.served import IServedProjects
from blizzard.hub.domain.runners.version import IVersionWindow
from blizzard.hub.tenancy import STORE_UNAVAILABLE


def served_projects(resolver: BulkProjectResolver) -> IServedProjects:
    """The tenant's projects as a served declaration resolves against them, in one read per add or registration."""
    return resolver


def fleet_service(
    registry: IWriteRunnerRegistry,
    hosts: IReadHostRegistry,
    transactions: IHubTransactions,
    routes: IReadChunkRouteRepository,
    records: IReadChunkRecordRepository,
    facts: IReadChunkFactsRepository,
    detach: DetachService,
    retired: RetiredRunnerGuard,
    served: IServedProjects,
    versions: IVersionWindow,
    clock: IClock,
) -> FleetService:
    """The tenant's fleet service, its liveness judged stale at the domain's default threshold."""
    return FleetService(
        registry=registry,
        hosts=hosts,
        transactions=transactions,
        routes=routes,
        records=records,
        facts=facts,
        detach=detach,
        retired=retired,
        served_projects=served,
        versions=versions,
        clock=clock,
    )


def apply_service(
    facts: IReadChunkFactsRepository,
    movement: IWriteChunkMovementRepository,
    decisions: IWriteChunkDecisionsRepository,
    escalations: IWriteChunkEscalationsRepository,
    transactions: IHubTransactions,
    route: IReadChunkRouteRepository,
    artifacts: IReadChunkArtifactsRepository,
    retired: RetiredRunnerGuard,
    clock: IClock,
    hub_step_wake: IHubStepWake,
    label: WorkRefLabel,
    closers: IWorkClosers,
    events: EventLogService,
) -> ApplyService:
    """The tenant's completion apply, its source closers judged against the tenant's configured work sources."""
    return ApplyService(
        facts=facts,
        movement=movement,
        decisions=decisions,
        escalations=escalations,
        transactions=transactions,
        route=route,
        artifacts=artifacts,
        retired=retired,
        clock=clock,
        hub_step_wake=hub_step_wake,
        label=label,
        closer_available=lambda source: closers.closer(source) is not None,
        events=events,
    )


def decision_service(
    facts: IReadChunkFactsRepository,
    route: IWriteChunkRouteRepository,
    decisions: IWriteChunkDecisionsRepository,
    transactions: IHubTransactions,
    retired: RetiredRunnerGuard,
    clock: IClock,
    closers: IWorkClosers,
    events: EventLogService,
) -> DecisionService:
    """The tenant's decision service, its source closers judged against the tenant's configured work sources."""
    return DecisionService(
        facts=facts,
        route=route,
        decisions=decisions,
        transactions=transactions,
        retired=retired,
        clock=clock,
        closer_available=lambda source: closers.closer(source) is not None,
        events=events,
    )


def register(registry: Registry, *, versions: Ref[IVersionWindow] | None) -> None:
    """Declare the fleet services once per tenant, and the version window's port over the window handed in — absent
    when the root supplies none."""
    registry.factory(served_projects, lifetime=Lifetime.TENANT)
    registry.service(DetachService, lifetime=Lifetime.TENANT)
    registry.factory(fleet_service, lifetime=Lifetime.TENANT)
    registry.service(HostRetirementService, lifetime=Lifetime.TENANT)
    registry.service(HostFactIngestService, lifetime=Lifetime.TENANT)
    registry.service(ClaimService, lifetime=Lifetime.TENANT)
    registry.factory(apply_service, lifetime=Lifetime.TENANT)
    registry.factory(decision_service, lifetime=Lifetime.TENANT)
    registry.service(RequeueService, lifetime=Lifetime.TENANT)
    registry.service(ReleaseService, lifetime=Lifetime.TENANT)
    registry.service(QuestionService, lifetime=Lifetime.TENANT)
    registry.service(FactIngestService, lifetime=Lifetime.TENANT)
    if versions is None:
        registry.absent(port(IVersionWindow), lifetime=Lifetime.PROCESS, label=STORE_UNAVAILABLE)
    else:
        registry.alias(port(IVersionWindow), versions)


#: Every tenant key :func:`register` declares, for the store-free graph to declare absent.
_TENANT_KEYS: tuple[type[Any], ...] = (
    IServedProjects,
    DetachService,
    FleetService,
    HostRetirementService,
    HostFactIngestService,
    ClaimService,
    ApplyService,
    DecisionService,
    RequeueService,
    ReleaseService,
    QuestionService,
    FactIngestService,
)


def register_absent(registry: Registry) -> None:
    """Declare the area's services and the version window's port absent, for the store-free graph."""
    for missing in _TENANT_KEYS:
        registry.absent(port(missing), lifetime=Lifetime.TENANT, label=STORE_UNAVAILABLE)
    registry.absent(port(IVersionWindow), lifetime=Lifetime.PROCESS, label=STORE_UNAVAILABLE)

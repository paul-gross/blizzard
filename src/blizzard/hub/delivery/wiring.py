"""The delivery area's registrations: the catalogue of the hub's actions, which the entrypoint borrows, and each
tenant's flow executor — the hub-node router, the turn runner, and the publishers a turn announces through.

The process holds the one action loop every tenant's passes run on, as a resource whose finalizer stops it, the action
pass driver over it, and the flow driver's step wake, which the domain names as ``IHubStepWake``. The loop is started
by the hosted app's lifespan; the process scope's close stops it after the tenant scopes, so a tenant's configured
clients close on the loop they run on.

The entrypoint resolves the actions from its environ and supplies them when the process scope opens, so no provider
reads the environment. The catalogue is a projection of that one value, keyed by the domain type, so a domain consumer
takes it without importing this package. The router answers the domain's chunk-write refusals, which the domain
reconciler names without importing it. Each executor class is registered under its own type, never under the driver's
protocol, so a tenant's turns and its announced turns are two registrations. The values a factory reads off
:class:`HubConfig` are read there, never keyed on their own. The store-free graph declares each key absent."""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

from blizzard.foundation.clock import IClock
from blizzard.foundation.platform_tracing.tracer import IPlatformTracer
from blizzard.foundation.registry.interface import Lifetime, Registry, port
from blizzard.hub.config import HubConfig
from blizzard.hub.delivery.action_loop import ActionLoop
from blizzard.hub.delivery.action_pass import ACTION_PASS_GRACE, ActionPassDriver, PassBound
from blizzard.hub.delivery.action_registry import HubActions
from blizzard.hub.delivery.flow_driver import StepWake
from blizzard.hub.delivery.flow_turn import ITurnPublisher, TurnRunner
from blizzard.hub.delivery.hub_node import HubNodeRouter
from blizzard.hub.domain.chunk.event_log import EventLogService
from blizzard.hub.domain.chunk.ports.artifacts import IWriteChunkArtifactsRepository
from blizzard.hub.domain.chunk.ports.decisions import IWriteChunkDecisionsRepository
from blizzard.hub.domain.chunk.ports.escalations import IWriteChunkEscalationsRepository
from blizzard.hub.domain.chunk.ports.facts import IReadChunkFactsRepository
from blizzard.hub.domain.chunk.ports.hub_exec import IWriteChunkHubExecRepository
from blizzard.hub.domain.chunk.ports.movement import IWriteChunkMovementRepository
from blizzard.hub.domain.chunk.ports.record import IReadChunkRecordRepository
from blizzard.hub.domain.config.forges import IReadForgeRepository
from blizzard.hub.domain.config.repositories import IReadRepositoryRecordRepository
from blizzard.hub.domain.delivery.ports.flows import IFlowChangePublisher, IHubStepRefusals, IWriteFlows
from blizzard.hub.domain.execution.apply import IHubStepWake
from blizzard.hub.domain.garden.delivery.service import GardenDeliveryRecorder
from blizzard.hub.domain.garden.review.service import ReviewFindingsRecorder
from blizzard.hub.domain.garden.run_context import IReadRunContextRepository
from blizzard.hub.domain.graph.actions import HubActionCatalogue
from blizzard.hub.domain.graph.model import IReadGraphRepository
from blizzard.hub.domain.kernel.transactions import IHubTransactions
from blizzard.hub.domain.projects.links import IReadProjectLinks
from blizzard.hub.domain.projects.model import IReadProjectRepository
from blizzard.hub.domain.projects.targets import ChunkTargets
from blizzard.hub.events.broker import EventBroker
from blizzard.hub.forge.registry import ForgeRegistry
from blizzard.hub.settings.source import HubConfigSource
from blizzard.hub.tenancy import STORE_UNAVAILABLE
from blizzard.hub.work_sources.source import IWorkSourceRegistry

#: The tenant keys :func:`register` declares beyond the catalogue, for the store-free graph to declare absent.
_TENANT_KEYS: tuple[type[Any], ...] = (
    HubNodeRouter,
    IHubStepRefusals,
    TurnRunner,
    ITurnPublisher,
    IFlowChangePublisher,
)


#: The process keys :func:`register` declares beyond the catalogue, for the store-free graph to declare absent.
_PROCESS_KEYS: tuple[type[Any], ...] = (
    ActionLoop,
    ActionPassDriver,
    StepWake,
    IHubStepWake,
)


def register(registry: Registry) -> None:
    """Declare the catalogue that resolves to the borrowed actions' own, the action loop and the pass driver over it,
    and the step wake under its own type and the domain's."""
    registry.factory(action_catalogue, lifetime=Lifetime.PROCESS)
    registry.resource(action_loop, lifetime=Lifetime.PROCESS)
    registry.factory(action_pass_driver, lifetime=Lifetime.PROCESS)
    step_wake = registry.service(StepWake, lifetime=Lifetime.PROCESS)
    registry.alias(port(IHubStepWake), step_wake)


def register_absent(registry: Registry) -> None:
    """Declare the action loop, the pass driver and the step wake absent, for the store-free graph."""
    for missing in _PROCESS_KEYS:
        registry.absent(port(missing), lifetime=Lifetime.PROCESS, label=STORE_UNAVAILABLE)


def register_tenant(registry: Registry) -> None:
    """Declare the tenant's hub-node router and the refusals it answers, its turn runner, and the publishers a turn
    announces through, each once per tenant."""
    router = registry.factory(hub_node_router, lifetime=Lifetime.TENANT)
    registry.alias(port(IHubStepRefusals), router)
    registry.factory(turn_runner, lifetime=Lifetime.TENANT)
    registry.factory(turn_publisher, lifetime=Lifetime.TENANT)
    registry.factory(flow_change_publisher, lifetime=Lifetime.TENANT)


def register_tenant_absent(registry: Registry) -> None:
    """Declare the tenant's flow executor absent, for the store-free graph."""
    for missing in _TENANT_KEYS:
        registry.absent(port(missing), lifetime=Lifetime.TENANT, label=STORE_UNAVAILABLE)


def action_loop() -> Iterator[ActionLoop]:
    """The one action loop every tenant's passes share, stopped when the process scope closes."""
    loop = ActionLoop()
    try:
        yield loop
    finally:
        loop.stop()


def action_pass_driver(loop: ActionLoop, config: HubConfigSource) -> ActionPassDriver:
    """Takes every tenant's action passes on the one loop, each bound by the live config's pass seconds past the
    default grace."""
    return ActionPassDriver(loop, lambda: PassBound(config.current().action_pass_seconds, ACTION_PASS_GRACE))


def action_catalogue(actions: HubActions) -> HubActionCatalogue:
    """The actions' catalogue."""
    return actions.catalogue


def turn_publisher(events: EventBroker) -> ITurnPublisher:
    """What a turn announces its event rows and flow facts through: the tenant's broker."""
    return events


def flow_change_publisher(events: EventBroker) -> IFlowChangePublisher:
    """What the flow driver announces a flow's change through: the tenant's broker."""
    return events


def hub_node_router(
    artifacts: IWriteChunkArtifactsRepository,
    hub_exec: IWriteChunkHubExecRepository,
    escalations: IWriteChunkEscalationsRepository,
    record: IReadChunkRecordRepository,
    transactions: IHubTransactions,
    events: EventLogService,
    clock: IClock,
    repositories: IReadRepositoryRecordRepository,
    forge_records: IReadForgeRepository,
    links: IReadProjectLinks,
    targets: ChunkTargets,
    projects: IReadProjectRepository,
    work_sources: IWorkSourceRegistry,
    movement: IWriteChunkMovementRepository,
    decisions: IWriteChunkDecisionsRepository,
    graphs: IReadGraphRepository,
    facts: IReadChunkFactsRepository,
    config: HubConfig,
) -> HubNodeRouter:
    """The tenant's hub-node router, following the latest graph as the boot config says."""
    return HubNodeRouter(
        artifacts=artifacts,
        hub_exec=hub_exec,
        escalations=escalations,
        record=record,
        transactions=transactions,
        events=events,
        clock=clock,
        repositories=repositories,
        forge_records=forge_records,
        links=links,
        targets=targets,
        projects=projects,
        work_sources=work_sources,
        movement=movement,
        decisions=decisions,
        graphs=graphs,
        facts=facts,
        follow_latest_default=config.follow_latest,
    )


def turn_runner(
    flows: IWriteFlows,
    facts: IReadChunkFactsRepository,
    record: IReadChunkRecordRepository,
    graphs: IReadGraphRepository,
    artifacts: IWriteChunkArtifactsRepository,
    transactions: IHubTransactions,
    router: HubNodeRouter,
    events: EventLogService,
    publisher: ITurnPublisher,
    clock: IClock,
    repositories: IReadRepositoryRecordRepository,
    forge_records: IReadForgeRepository,
    links: IReadProjectLinks,
    targets: ChunkTargets,
    projects: IReadProjectRepository,
    actions: HubActions,
    passes: ActionPassDriver,
    forges: ForgeRegistry,
    review_findings: ReviewFindingsRecorder,
    garden_delivery: GardenDeliveryRecorder,
    run_context: IReadRunContextRepository,
    work_sources: IWorkSourceRegistry,
    tracer: IPlatformTracer,
    config: HubConfig,
    config_source: HubConfigSource,
) -> TurnRunner:
    """The tenant's turn runner: each turn is bound to the config the settings graph resolves when it starts, and
    announces its public URL as the boot config names it."""
    return TurnRunner(
        flows=flows,
        facts=facts,
        record=record,
        graphs=graphs,
        artifacts=artifacts,
        transactions=transactions,
        router=router,
        events=events,
        publisher=publisher,
        clock=clock,
        repositories=repositories,
        forge_records=forge_records,
        links=links,
        targets=targets,
        projects=projects,
        actions=actions,
        passes=passes,
        forges=forges,
        review_findings=review_findings,
        garden_delivery=garden_delivery,
        run_context=run_context,
        public_url=config.public_url,
        work_sources=work_sources,
        tracer=tracer,
        pass_config=config_source.resolved,
    )

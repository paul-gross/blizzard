"""The hub's fleet-service composition (``bzh:dependency-injection``).

One place the store-backed collaborators are constructed and injected: the chunk and
graph stores, the four domain services (ingest, claim, apply, graph-mint) and the
deliver coordinator, the PM read seam, and the event broker. :func:`build_services`
is called by the ``host`` composition root (:func:`blizzard.hub.app.build_hosted_app`)
and by tests, which swap the forge and PM seams for fakes by type. The store-free
export/unit app builds no services — the fleet routes report the store is unwired
rather than serving on a missing database.

Controllers read the stores through their **read** Protocols and mutate only through
the services (``bzh:controller-read-only``); both variants are the one
:class:`~blizzard.hub.store.internal.chunk_store.ChunkStore` instance, so a request
sees a consistent view.
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import Engine

from blizzard.foundation.clock import IClock, SystemClock
from blizzard.hub.delivery.check import DeliveryCheckService
from blizzard.hub.delivery.coordinator import MergeQueueCoordinator
from blizzard.hub.delivery.forge import IForgeDelivery
from blizzard.hub.domain.apply import ApplyService
from blizzard.hub.domain.claim import ClaimService
from blizzard.hub.domain.decisions import DecisionService, RequeueService
from blizzard.hub.domain.facts import FactIngestService, RunnerFactsService
from blizzard.hub.domain.graph import GraphDoc, IReadGraphRepository
from blizzard.hub.domain.graph_authoring import GraphMintService
from blizzard.hub.domain.ingest import IngestService
from blizzard.hub.domain.promote import PromoteService
from blizzard.hub.domain.questions import QuestionService
from blizzard.hub.domain.queue import GroupService, QueueService
from blizzard.hub.domain.registry import FleetService
from blizzard.hub.domain.work import IReadChunkRepository
from blizzard.hub.events.broker import EventBroker
from blizzard.hub.graphs import default_graph_yaml, load_default_graph_doc
from blizzard.hub.pm.source import IPmSource
from blizzard.hub.store.internal.chunk_store import ChunkStore
from blizzard.hub.store.internal.graph_store import GraphStore
from blizzard.hub.store.internal.runner_registry_store import RunnerRegistryStore


@dataclass(frozen=True)
class HubServices:
    """The wired fleet collaborators, stashed on ``app.state.services``."""

    chunks: IReadChunkRepository
    graphs: IReadGraphRepository
    ingest: IngestService
    promote: PromoteService
    claim: ClaimService
    apply: ApplyService
    decisions: DecisionService
    requeue: RequeueService
    facts: FactIngestService
    graph_mint: GraphMintService
    runner_facts: RunnerFactsService
    questions: QuestionService
    queue: QueueService
    group: GroupService
    fleet: FleetService
    delivery_check: DeliveryCheckService
    events: EventBroker
    clock: IClock
    default_graph_doc: GraphDoc
    default_graph_yaml: str
    pm_source: IPmSource | None = None


def build_services(
    engine: Engine,
    *,
    forge: IForgeDelivery,
    events: EventBroker,
    pm_source: IPmSource | None = None,
    clock: IClock | None = None,
    base_branch: str = "main",
) -> HubServices:
    """Construct and wire every fleet service over a migrated store engine.

    ``base_branch`` is the branch every PR/merge targets (D-060) — ``main`` for the
    verification forge's bare origins, set to a real repo's default (e.g. ``master``) at
    the ``host`` composition root from ``BZ_FORGE_BASE_BRANCH``.
    """
    clock = clock or SystemClock()
    chunk_store = ChunkStore(engine, clock)
    graph_store = GraphStore(engine)
    registry_store = RunnerRegistryStore(engine)
    coordinator = MergeQueueCoordinator(chunks=chunk_store, forge=forge, clock=clock, base_branch=base_branch)
    return HubServices(
        chunks=chunk_store,
        graphs=graph_store,
        ingest=IngestService(chunks=chunk_store, clock=clock),
        promote=PromoteService(chunks=chunk_store, clock=clock),
        claim=ClaimService(chunks=chunk_store, clock=clock),
        apply=ApplyService(chunks=chunk_store, coordinator=coordinator, clock=clock),
        decisions=DecisionService(chunks=chunk_store, clock=clock),
        requeue=RequeueService(chunks=chunk_store, clock=clock),
        facts=FactIngestService(chunks=chunk_store, clock=clock),
        graph_mint=GraphMintService(graphs=graph_store, clock=clock),
        runner_facts=RunnerFactsService(chunks=chunk_store, clock=clock),
        questions=QuestionService(chunks=chunk_store, clock=clock),
        queue=QueueService(chunks=chunk_store, clock=clock),
        group=GroupService(chunks=chunk_store, clock=clock),
        fleet=FleetService(registry=registry_store, clock=clock),
        delivery_check=DeliveryCheckService(chunks=chunk_store, forge=forge, clock=clock),
        events=events,
        clock=clock,
        default_graph_doc=load_default_graph_doc(),
        default_graph_yaml=default_graph_yaml(),
        pm_source=pm_source,
    )

"""The hub's fleet-service composition (``bzh:dependency-injection``).

One place the store-backed collaborators are constructed and injected: the chunk and
graph stores, the four domain services (ingest, claim, apply, graph-mint) and the
generic hub command node executor, the PM read seam, and the event broker. :func:`build_services`
is called by the ``host`` composition root (:func:`blizzard.hub.app.build_hosted_app`)
and by tests, which swap the PM seam for fakes by type. The store-free export/unit app
builds no services — the fleet routes report the store is unwired rather than serving
on a missing database.

Controllers read the stores through their **read** Protocols and mutate only through
the services (``bzh:controller-read-only``); both variants are the one
:class:`~blizzard.hub.store.internal.chunk_store.ChunkStore` instance, so a request
sees a consistent view.
"""

from __future__ import annotations

import tempfile
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy import Engine

from blizzard.foundation.clock import IClock, SystemClock
from blizzard.hub.delivery.command_runner import IHubCommandRunner
from blizzard.hub.delivery.hub_node import HubNodeExecutor
from blizzard.hub.delivery.internal.hub_command_runner import SubprocessHubCommandRunner
from blizzard.hub.delivery.internal.hub_workdir import FilesystemHubWorkdir
from blizzard.hub.delivery.workdir import IHubWorkdir
from blizzard.hub.domain.apply import ApplyService
from blizzard.hub.domain.claim import ClaimService
from blizzard.hub.domain.decisions import DecisionService, RequeueService
from blizzard.hub.domain.detach import DetachService
from blizzard.hub.domain.edit import EditService
from blizzard.hub.domain.facts import FactIngestService, RunnerFactsService
from blizzard.hub.domain.graph import GraphDoc, IReadGraphRepository
from blizzard.hub.domain.graph_authoring import GraphMintService
from blizzard.hub.domain.ingest import IngestService
from blizzard.hub.domain.pause import PauseService
from blizzard.hub.domain.promote import PromoteService
from blizzard.hub.domain.questions import QuestionService
from blizzard.hub.domain.queue import GroupService, QueueService
from blizzard.hub.domain.registry import FleetService
from blizzard.hub.domain.work import IReadChunkRepository
from blizzard.hub.events.broker import EventBroker
from blizzard.hub.graphs import default_graph_yaml, load_default_graph_doc
from blizzard.hub.pm.source import IPmSourceRegistry
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
    detach: DetachService
    pause: PauseService
    edit: EditService
    facts: FactIngestService
    graph_mint: GraphMintService
    runner_facts: RunnerFactsService
    questions: QuestionService
    queue: QueueService
    group: GroupService
    fleet: FleetService
    hub_node: HubNodeExecutor
    events: EventBroker
    clock: IClock
    default_graph_doc: GraphDoc
    default_graph_yaml: str
    pm: IPmSourceRegistry


def build_services(
    engine: Engine,
    *,
    events: EventBroker,
    pm: IPmSourceRegistry,
    clock: IClock | None = None,
    base_branch: str = "main",
    hub_command_runner: IHubCommandRunner | None = None,
    hub_workdir: IHubWorkdir | None = None,
    hub_workdir_root: Path | None = None,
    hub_marker_callback_base_url: str = "",
    forge_url: str | None = None,
    forge_token: str | None = None,
    forge_owner: str | None = None,
) -> HubServices:
    """Construct and wire every fleet service over a migrated store engine.

    ``base_branch`` is the branch every PR/merge targets — ``main`` for the
    verification forge's bare origins, set to a real repo's default (e.g. ``master``) at
    the ``host`` composition root from ``BZ_FORGE_BASE_BRANCH``. ``hub_command_runner``
    / ``hub_workdir`` are the generic hub command node's mechanism seams (#65) — tests
    inject fakes; the ``host`` composition root leaves them ``None`` for the real
    subprocess/filesystem adapters, rooted under ``hub_workdir_root``. ``forge_owner``
    is injected into a hub command node's env (``BZ_FORGE_OWNER``) so its own
    ``run:`` script can qualify a bare (owner-less) repo the same way its own
    ``qualify_repo`` does (e.g. ``hub/graphs/scripts/land_default.py``).
    """
    clock = clock or SystemClock()
    chunk_store = ChunkStore(engine, clock)
    graph_store = GraphStore(engine)
    registry_store = RunnerRegistryStore(engine)
    hub_node = HubNodeExecutor(
        chunks=chunk_store,
        runner=hub_command_runner or SubprocessHubCommandRunner(),
        workdir=hub_workdir
        or FilesystemHubWorkdir(hub_workdir_root or Path(tempfile.gettempdir()) / "blizzard-hub-workdirs"),
        clock=clock,
        base_branch=base_branch,
        marker_callback_base_url=hub_marker_callback_base_url,
        forge_url=forge_url,
        forge_token=forge_token,
        forge_owner=forge_owner,
    )
    # One fleet service, shared: the API's pause routes and the fact ingest both land
    # registry facts, and two instances would be two of the same thing (issue #43).
    fleet = FleetService(registry=registry_store, clock=clock)
    return HubServices(
        chunks=chunk_store,
        graphs=graph_store,
        ingest=IngestService(chunks=chunk_store, clock=clock),
        promote=PromoteService(chunks=chunk_store, clock=clock),
        claim=ClaimService(chunks=chunk_store, registry=registry_store, clock=clock),
        apply=ApplyService(chunks=chunk_store, clock=clock, hub_node_executor=hub_node),
        decisions=DecisionService(chunks=chunk_store, clock=clock),
        requeue=RequeueService(chunks=chunk_store, clock=clock),
        detach=DetachService(chunks=chunk_store, clock=clock),
        pause=PauseService(chunks=chunk_store, clock=clock),
        edit=EditService(chunks=chunk_store),
        facts=FactIngestService(chunks=chunk_store, fleet=fleet, clock=clock),
        graph_mint=GraphMintService(graphs=graph_store, clock=clock),
        runner_facts=RunnerFactsService(chunks=chunk_store, clock=clock),
        questions=QuestionService(chunks=chunk_store, clock=clock),
        queue=QueueService(chunks=chunk_store, clock=clock),
        group=GroupService(chunks=chunk_store, clock=clock),
        fleet=fleet,
        hub_node=hub_node,
        events=events,
        clock=clock,
        default_graph_doc=load_default_graph_doc(),
        default_graph_yaml=default_graph_yaml(),
        pm=pm,
    )

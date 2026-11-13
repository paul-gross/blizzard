"""The review-findings recorder world (test support): a seeded ``record-findings`` chunk, its staged
``review-finding-delta`` asset, and a delivery through the recorder over the hub's store. Shared by the component
tests that deliver review findings."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from sqlalchemy import select

from blizzard.foundation.node_steps import Executor, JudgedBy, SessionMode
from blizzard.hub.api.garden_formats import GardenFormats
from blizzard.hub.domain.chunk.model import WorkItemAuthor
from blizzard.hub.domain.garden.review.materialize import ReviewFindingsMaterialize, ReviewFindingsOutcome
from blizzard.hub.domain.garden.review.service import ReviewFindingsRecorder
from blizzard.hub.domain.garden.review.validation import ReviewFindingsRejected
from blizzard.hub.domain.graph.model import Graph, Node
from blizzard.hub.store import schema as s
from blizzard.hub.store.internal.finding_store import FindingStore
from blizzard.hub.store.internal.graph_store import GraphStore
from blizzard.hub.store.internal.review_findings_store import ReviewFindingsStore
from blizzard.hub.store.internal.scope_store import ScopeStore
from blizzard.hub.store.transactions import HubTransactions
from tests.support.hub_node_artifacts import record_hub_asset
from tests.support.scaffolding import (
    HubHarness,
    chunk_stores,
    hub_scoped,
    hub_store_connections,
    seed_work_item,
)

NODE_ID = "nd_record_findings"
EPOCH = 1
ARTIFACT_NAME = "review-finding-delta"


def _record_findings_node(node_id: str = NODE_ID, *, graph_id: str = "gr_record_findings") -> Node:
    return Node(
        node_id=node_id,
        graph_id=graph_id,
        name="record-findings",
        executor=Executor.HUB,
        prompt=None,
        checks=[],
        produces=[],
        session=SessionMode.FRESH,
        judged_by=JudgedBy.WORKER,
        retries_max=None,
        retries_exhausted=None,
    )


def seed_chunk(hub: HubHarness) -> str:
    store_connections = hub_store_connections(hub.engine)
    node = _record_findings_node()
    graph = Graph(
        graph_id="gr_record_findings",
        name="g",
        entry_node_id=node.node_id,
        nodes=[node],
        edges=[],
        created_at=hub.clock.now(),
    )
    GraphStore(store_connections).mint(graph, definition_yaml="", at=hub.clock.now())
    item = seed_work_item(
        store_connections, graph_id="gr_record_findings", author=WorkItemAuthor.user("u_1"), at=hub.clock.now()
    )
    return f"ch_{item.ref}"


def record_artifact(hub: HubHarness, chunk_id: str, *, content: str, epoch: int = EPOCH) -> None:
    assert record_hub_asset(
        hub,
        chunk_id,
        node_id=NODE_ID,
        node_name="record-findings",
        epoch=epoch,
        name=ARTIFACT_NAME,
        content=content,
    )


@dataclass(frozen=True)
class Delivered:
    outcome: Literal["recorded", "invalid", "fenced"]
    detail: str = ""


def deliver(hub: HubHarness, chunk_id: str, *, epoch: int = EPOCH) -> Delivered:
    """Deliver the chunk's newest review-finding delta from its record-findings node at ``epoch``."""
    chunk = hub.services.chunks.record.get(chunk_id)
    assert chunk is not None, chunk_id
    graph = hub.services.graphs.get(chunk.graph_id)
    node = graph.node_by_id(NODE_ID) if graph is not None else None
    assert node is not None
    connections = hub_store_connections(hub.engine)
    recorder = ReviewFindingsRecorder(
        artifacts=chunk_stores(hub.engine, hub.clock).artifacts,
        materialize=ReviewFindingsMaterialize(
            delivery=ReviewFindingsStore(connections),
            findings=FindingStore(connections),
            scopes=ScopeStore(connections),
            facts=chunk_stores(hub.engine, hub.clock).facts,
            transactions=HubTransactions(connections),
            clock=hub.clock,
        ),
        formats=GardenFormats(),
    )
    try:
        outcome = recorder.record(chunk=chunk, node=node, epoch=epoch)
    except ReviewFindingsRejected as exc:
        return Delivered("invalid", str(exc))
    return Delivered("fenced") if outcome is ReviewFindingsOutcome.FENCED else Delivered("recorded")


def finding_count(hub: HubHarness) -> int:
    with hub.engine.begin() as conn:
        return len(conn.execute(hub_scoped(select(s.findings))).all())

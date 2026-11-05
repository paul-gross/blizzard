"""Helpers for the garden delivery recorder's tests: a chunk standing at a hub delivery node, its artifacts recorded
through the artifacts repository, and a delivery taken through the recorder a ``garden-deliver`` pass drives."""

from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Literal

from sqlalchemy import insert, select

from blizzard.foundation.node_steps import Executor, JudgedBy, SessionMode
from blizzard.hub.api.garden_formats import GardenFormats
from blizzard.hub.domain.chunk.model import WorkItemAuthor
from blizzard.hub.domain.garden.delivery.materialize import DeliveryOutcome, GardenDelivery
from blizzard.hub.domain.garden.delivery.service import GardenDeliveryRecorder
from blizzard.hub.domain.garden.delivery.validation import GardenDeliveryRejected
from blizzard.hub.domain.garden.findings.bucket import FindingBucketReader
from blizzard.hub.domain.garden.run_context import RunContext
from blizzard.hub.domain.graph.model import Graph, Node
from blizzard.hub.store import schema as s
from blizzard.hub.store.internal.finding_store import FindingStore
from blizzard.hub.store.internal.garden_delivery_store import GardenDeliveryStore
from blizzard.hub.store.internal.graph_store import GraphStore
from blizzard.hub.store.internal.run_context_store import RunContextStore
from tests.support.hub_node_artifacts import record_hub_asset
from tests.support.scaffolding import (
    HubHarness,
    chunk_stores,
    default_project,
    hub_scoped,
    hub_store_connections,
    seed_work_item,
    stamp,
)

_NOW = datetime(2026, 7, 16, 12, 0, 0, tzinfo=UTC)


_NODE_ID = "nd_deliver"


_EPOCH = 1


_ROUTINE = "nightly"


SCOPE = "blizzard"


def seed_scope(hub: HubHarness, slug: str) -> None:
    with hub.engine.begin() as conn:
        conn.execute(insert(s.scopes).values(stamp(s.scopes, slug=slug, description="", created_at=_NOW)))


def _deliver_node(node_id: str = _NODE_ID, *, graph_id: str = "gr_delivery") -> Node:
    return Node(
        node_id=node_id,
        graph_id=graph_id,
        name="deliver",
        executor=Executor.HUB,
        prompt=None,
        checks=[],
        produces=[],
        session=SessionMode.FRESH,
        judged_by=JudgedBy.WORKER,
        retries_max=None,
        retries_exhausted=None,
    )


def seed_chunk(hub: HubHarness, *, with_run_context: bool = True) -> str:
    """A work item with its own resting chunk (its own two-step mint), plus a
    recorded run context for it unless ``with_run_context`` is False. The graph carries the one delivery node
    :func:`deliver` delivers at."""
    store_connections = hub_store_connections(hub.engine)
    node = _deliver_node()
    graph = Graph(graph_id="gr_delivery", name="g", entry_node_id=node.node_id, nodes=[node], edges=[], created_at=_NOW)
    GraphStore(store_connections).mint(graph, definition_yaml="", at=_NOW)
    item = seed_work_item(store_connections, graph_id="gr_delivery", author=WorkItemAuthor.user("u_1"), at=_NOW)
    if with_run_context:
        RunContextStore(store_connections).record(
            item.work_item_id,
            RunContext(project_id=default_project(hub.engine), routine_name=_ROUTINE, scope_slug=SCOPE, mode="dry_run"),
        )
    return f"ch_{item.ref}"


def record_artifact(hub: HubHarness, chunk_id: str, *, name: str, content: str, epoch: int = _EPOCH) -> None:
    assert record_hub_asset(
        hub, chunk_id, node_id=_NODE_ID, node_name="deliver", epoch=epoch, name=name, content=content
    )


def garden_delta(
    *, scope: str = SCOPE, revisions: dict[str, str] | None = None, findings: list[dict[str, object]] | None = None
) -> str:
    return json.dumps({"scope": scope, "revisions": revisions or {}, "measurement": None, "findings": findings or []})


def add_op(
    *, class_: str = "c", locus: str = "a.py:1", summary: str = "s", ref: str | None = None
) -> dict[str, object]:
    op: dict[str, object] = {"op": "add", "class": class_, "locus": locus, "summary": summary}
    if ref is not None:
        op["ref"] = ref
    return op


@dataclass(frozen=True)
class Delivered:
    """What the recorder made of one delivery: ``invalid`` carries its refusal in ``detail``; ``fenced`` is a
    delivery the chunk was stopped or restarted past."""

    outcome: Literal["recorded", "invalid", "fenced"]
    detail: str = ""


def garden_delivery_recorder(hub: HubHarness) -> GardenDeliveryRecorder:
    """The recorder a ``garden-deliver`` pass records through, over the hub's own store."""
    connections = hub_store_connections(hub.engine)
    return GardenDeliveryRecorder(
        artifacts=chunk_stores(hub.engine, hub.clock).artifacts,
        buckets=FindingBucketReader(FindingStore(connections)),
        materialize=GardenDelivery(delivery=GardenDeliveryStore(connections), clock=hub.clock),
        formats=GardenFormats(),
        resolve_commit_for=None,
    )


def deliver(
    hub: HubHarness,
    chunk_id: str,
    *,
    delta: Sequence[str] = (),
    proposals: Sequence[str] = (),
    epoch: int = _EPOCH,
) -> Delivered:
    """Deliver ``delta`` and ``proposals`` for the chunk's delivery node at ``epoch``, under its run context."""
    chunk = hub.services.chunks.record.get(chunk_id)
    assert chunk is not None, chunk_id
    graph = hub.services.graphs.get(chunk.graph_id)
    node = graph.node_by_id(_NODE_ID) if graph is not None else None
    assert node is not None
    run = hub.services.run_context.for_chunk(chunk)
    assert run is not None, chunk_id
    try:
        outcome = garden_delivery_recorder(hub).record(
            chunk=chunk, node=node, epoch=epoch, run=run, delta_names=delta, proposal_names=proposals
        )
    except GardenDeliveryRejected as exc:
        return Delivered("invalid", str(exc))
    return Delivered("fenced") if outcome is DeliveryOutcome.FENCED else Delivered("recorded")


def finding_count(hub: HubHarness) -> int:
    with hub.engine.begin() as conn:
        return conn.execute(hub_scoped(select(s.findings))).all().__len__()

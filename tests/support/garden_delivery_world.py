"""Helpers for the garden delivery api tests, shared with other test modules."""

from __future__ import annotations

import json
from collections.abc import Sequence
from datetime import UTC, datetime

from sqlalchemy import insert, select

from blizzard.foundation.node_steps import Executor, JudgedBy, SessionMode
from blizzard.hub.domain.chunk.model import WorkItemAuthor
from blizzard.hub.domain.garden.run_context import RunContext
from blizzard.hub.domain.graph.model import Graph, Node
from blizzard.hub.store import schema as s
from blizzard.hub.store.internal.graph_store import GraphStore
from blizzard.hub.store.internal.run_context_store import RunContextStore
from tests.support.scaffolding import (
    HubHarness,
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
    recorded run context for it unless ``with_run_context`` is False — the
    chunk id the delivery route resolves through. The graph carries the one node the
    route's ``node_id`` names, so ``graph.node_by_id`` resolves it."""
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
    assert hub.services.hub_markers.record_marker(
        chunk_id, node_id=_NODE_ID, node_name="deliver", epoch=epoch, name=name, content=content
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


def post(
    hub: HubHarness,
    chunk_id: str,
    *,
    delta: Sequence[str] = (),
    proposals: Sequence[str] = (),
    node_id: str = _NODE_ID,
    epoch: int = _EPOCH,
):
    return hub.client.post(
        f"/api/chunks/{chunk_id}/garden-delivery?node_id={node_id}&epoch={epoch}",
        json={"delta": list(delta), "proposals": list(proposals)},
    )


def finding_count(hub: HubHarness) -> int:
    with hub.engine.begin() as conn:
        return conn.execute(hub_scoped(select(s.findings))).all().__len__()

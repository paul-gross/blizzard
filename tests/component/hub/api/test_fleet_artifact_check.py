"""``POST /api/fleet/chunks/{chunk_id}/artifact-check`` — the verdict on content a worker is about to stage
(component tier). The verdict rides a ``200`` body; only an unknown chunk is a ``404``."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import Engine

from blizzard.foundation.node_steps import Executor, JudgedBy, SessionMode
from blizzard.hub.domain.chunk.model import WorkItemAuthor
from blizzard.hub.domain.graph.model import Graph, Node
from blizzard.hub.store.internal.graph_store import GraphStore
from tests.support import build_hub, hub_store_connections, seed_work_item

_NOW = datetime(2026, 9, 4, 12, 0, 0, tzinfo=UTC)


def _seed_deliver_chunk(engine: Engine) -> str:
    connections = hub_store_connections(engine)
    node = Node(
        node_id="nd_deliver",
        graph_id="gr_check",
        name="deliver",
        executor=Executor.HUB,
        prompt=None,
        checks=[],
        produces=[],
        session=SessionMode.FRESH,
        judged_by=JudgedBy.WORKER,
        retries_max=None,
        retries_exhausted=None,
        action="garden-deliver",
        action_args={"delta": ("delta",), "proposals": ("docket",)},
    )
    graph = Graph(graph_id="gr_check", name="g", entry_node_id=node.node_id, nodes=[node], edges=[], created_at=_NOW)
    GraphStore(connections).mint(graph, definition_yaml="", at=_NOW)
    item = seed_work_item(connections, graph_id="gr_check", author=WorkItemAuthor.user("u_1"), at=_NOW)
    return f"ch_{item.ref}"


def _check(tmp_path: Path, name: str, content: str) -> tuple[int, dict[str, object]]:
    hub = build_hub(tmp_path)
    chunk_id = _seed_deliver_chunk(hub.engine)
    resp = hub.client.post(f"/api/fleet/chunks/{chunk_id}/artifact-check", json={"name": name, "content": content})
    return resp.status_code, resp.json()


def test_an_invalid_delta_answers_200_with_the_refusal(tmp_path: Path) -> None:
    content = json.dumps({"scope": "s", "revisions": {}, "measurement": {"a": 1}, "findings": []})
    status_code, body = _check(tmp_path, "delta", content)
    assert status_code == 200
    assert body["refusal"] == (
        "artifact 'delta' does not match the finding-delta shape: measurement: Input should be a valid string"
    )


def test_a_valid_delta_answers_200_with_no_refusal(tmp_path: Path) -> None:
    content = json.dumps({"scope": "s", "revisions": {}, "measurement": None, "findings": []})
    assert _check(tmp_path, "delta", content) == (200, {"refusal": None})


def test_an_unvalidated_name_answers_200_with_no_refusal(tmp_path: Path) -> None:
    assert _check(tmp_path, "survey", "[{not json") == (200, {"refusal": None})


def test_an_unknown_chunk_is_404(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    resp = hub.client.post("/api/fleet/chunks/ch_ghost/artifact-check", json={"name": "delta", "content": "x"})
    assert resp.status_code == 404

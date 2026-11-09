"""``POST /api/fleet/chunks/{chunk_id}/artifact-check`` — the verdict on content a worker is about to stage
(component tier). The verdict rides a ``200`` body; only an unknown chunk is a ``404``."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest
from sqlalchemy import Engine

from blizzard.foundation.node_steps import Executor, JudgedBy, SessionMode
from blizzard.hub.domain.chunk.model import WorkItemAuthor
from blizzard.hub.domain.graph.model import Graph, Node
from blizzard.hub.store.internal.graph_store import GraphStore
from blizzard.hub.store.scoped import ScopedConnections
from blizzard.wire.chunk import ChunkNotFoundProblem
from blizzard.wire.route import PinnedGraphMissingProblem
from tests.support.refusal_observation import RefusalObservation, refusal_mismatches
from tests.support.scaffolding import build_hub, hub_store_connections, seed_work_item

_NOW = datetime(2026, 9, 4, 12, 0, 0, tzinfo=UTC)


def _node(
    graph_id: str, name: str, *, action: str | None = None, args: dict[str, tuple[str, ...]] | None = None
) -> Node:
    return Node(
        node_id=f"nd_{graph_id}_{name}",
        graph_id=graph_id,
        name=name,
        executor=Executor.HUB if action else Executor.RUNNER,
        prompt=None,
        checks=[],
        produces=[],
        session=SessionMode.FRESH,
        judged_by=JudgedBy.WORKER,
        retries_max=None,
        retries_exhausted=None,
        action=action,
        action_args=args or {},
    )


def _mint(connections: ScopedConnections, graph_id: str, nodes: list[Node]) -> None:
    graph = Graph(
        graph_id=graph_id, name=graph_id, entry_node_id=nodes[0].node_id, nodes=nodes, edges=[], created_at=_NOW
    )
    GraphStore(connections).mint(graph, definition_yaml="", at=_NOW)


def _chunk_on(connections: ScopedConnections, graph_id: str) -> str:
    item = seed_work_item(connections, graph_id=graph_id, author=WorkItemAuthor.user("u_1"), at=_NOW)
    return f"ch_{item.ref}"


def _seed_deliver_chunk(engine: Engine) -> str:
    connections = hub_store_connections(engine)
    node = _node("gr_check", "deliver", action="garden-deliver", args={"delta": ("delta",), "proposals": ("docket",)})
    _mint(connections, "gr_check", [node])
    return _chunk_on(connections, "gr_check")


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
    assert resp.json()["detail"] == "unknown chunk ch_ghost"
    assert hub.app is not None
    assert (
        refusal_mismatches(
            hub.app.openapi(),
            RefusalObservation(
                "post",
                "/api/fleet/chunks/{chunk_id}/artifact-check",
                404,
                resp.headers["content-type"],
                ChunkNotFoundProblem,
                resp.json(),
            ),
        )
        == []
    )


def test_missing_graph_is_declared_and_does_not_mutate(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    hub = build_hub(tmp_path)
    chunk_id = _seed_deliver_chunk(hub.engine)
    before = hub.services.chunks.record.get(chunk_id)
    facts = hub.services.chunks.facts.load_facts(chunk_id)

    def missing_graph(graph_id: str) -> None:
        return None

    monkeypatch.setattr(hub.services.graphs, "get", missing_graph)
    url = f"/api/fleet/chunks/{chunk_id}/artifact-check"
    response = hub.client.post(url, json={"name": "delta", "content": "x"})
    assert response.status_code == 500
    assert response.json()["detail"] == "chunk's pinned graph is missing"
    assert response.json()["instance"] == url
    assert hub.app is not None
    assert (
        refusal_mismatches(
            hub.app.openapi(),
            RefusalObservation(
                "post",
                "/api/fleet/chunks/{chunk_id}/artifact-check",
                500,
                response.headers["content-type"],
                PinnedGraphMissingProblem,
                response.json(),
            ),
        )
        == []
    )
    assert hub.services.chunks.record.get(chunk_id) == before
    assert hub.services.chunks.facts.load_facts(chunk_id) == facts


_VALID_DELTA = json.dumps({"entries": [{"ref": "F1", "disposition": "fixed"}]})


def _review_check(tmp_path: Path, *, with_consumer: bool, content: str) -> tuple[int, dict[str, object]]:
    hub = build_hub(tmp_path)
    connections = hub_store_connections(hub.engine)
    # A graph that has a consumer is always minted, so the chunk without one is judged by its own pin.
    _mint(connections, "gr_with", [_node("gr_with", "work"), _node("gr_with", "record", action="record-findings")])
    _mint(connections, "gr_without", [_node("gr_without", "work")])
    chunk_id = _chunk_on(connections, "gr_with" if with_consumer else "gr_without")
    resp = hub.client.post(
        f"/api/fleet/chunks/{chunk_id}/artifact-check", json={"name": "review-finding-delta", "content": content}
    )
    return resp.status_code, resp.json()


def test_a_malformed_review_delta_is_a_200_refusal_when_the_pinned_graph_records_findings(tmp_path: Path) -> None:
    status_code, body = _review_check(tmp_path, with_consumer=True, content="none")
    assert status_code == 200
    refusal = body["refusal"]
    assert isinstance(refusal, str) and "review-finding-delta shape" in refusal


def test_a_valid_review_delta_has_no_refusal_when_the_pinned_graph_records_findings(tmp_path: Path) -> None:
    assert _review_check(tmp_path, with_consumer=True, content=_VALID_DELTA) == (200, {"refusal": None})


def test_a_review_delta_is_never_refused_on_a_pin_without_record_findings(tmp_path: Path) -> None:
    assert _review_check(tmp_path, with_consumer=False, content="none") == (200, {"refusal": None})

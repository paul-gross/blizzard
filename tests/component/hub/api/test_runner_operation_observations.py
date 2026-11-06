"""Runner operation refusals observed against their OpenAPI declarations."""

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from httpx2 import Response
from pydantic import BaseModel

from blizzard.hub.domain.chunk.model import WorkItemAuthor
from blizzard.hub.domain.garden.run_context import RunContext
from blizzard.hub.store.internal.run_context_store import RunContextStore
from blizzard.wire.chunk import ChunkNotFoundProblem, ChunkNotPausableProblem, NoCurrentNodeProblem
from blizzard.wire.route import MalformedCursorProblem
from tests.support.refusal_observation import RefusalObservation, refusal_mismatches
from tests.support.scaffolding import build_hub, default_project, hub_store_connections, seed_scope, seed_work_item


def _observe(
    spec: dict[str, Any], response: Response, method: str, template: str, model: type[BaseModel], status: int
) -> None:
    assert response.status_code == status, response.text
    assert response.headers["content-type"] == "application/problem+json"
    assert response.json()["instance"] == response.request.url.path
    observation = RefusalObservation(method, template, status, response.headers["content-type"], model, response.json())
    assert refusal_mismatches(spec, observation) == []


@pytest.mark.parametrize("operation", ["pm-items", "pause", "resume", "completions", "decisions"])
def test_missing_chunk(tmp_path: Path, operation: str) -> None:
    hub = build_hub(tmp_path)
    assert hub.app is not None
    path = f"/api/fleet/chunks/ch_missing/{operation}"
    method = "get" if operation == "pm-items" else "post"
    if method == "get":
        response = hub.client.get(path)
    else:
        response = hub.client.post(path, json={"epoch": 1, "from_node_id": "nd_1", "choice": "done"})
    assert response.json()["detail"] == "unknown chunk ch_missing"
    _observe(
        hub.app.openapi(), response, method, f"/api/fleet/chunks/{{chunk_id}}/{operation}", ChunkNotFoundProblem, 404
    )


def test_stopped_chunk_cannot_pause_or_provide_current_node(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    assert hub.app is not None
    created = hub.client.post("/api/chunks", json={"tokens": ["default:1"]})
    assert created.status_code == 201, created.text
    chunk = created.json()["chunk_id"]
    assert hub.client.post(f"/api/chunks/{chunk}/stop", json={"by": "operator"}).status_code == 202
    pause = hub.client.post(f"/api/fleet/chunks/{chunk}/pause")
    _observe(hub.app.openapi(), pause, "post", "/api/fleet/chunks/{chunk_id}/pause", ChunkNotPausableProblem, 409)
    envelope = hub.client.get(f"/api/fleet/chunks/{chunk}/envelope")
    _observe(hub.app.openapi(), envelope, "get", "/api/fleet/chunks/{chunk_id}/envelope", NoCurrentNodeProblem, 409)


def test_malformed_routine_proposal_cursor(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    assert hub.app is not None
    connections = hub_store_connections(hub.engine)
    now = datetime(2026, 9, 2, tzinfo=UTC)
    item = seed_work_item(connections, graph_id="gr_garden", author=WorkItemAuthor.user("u_1"), at=now)
    seed_scope(connections, "blizzard", at=now)
    RunContextStore(connections).record(
        item.work_item_id,
        RunContext(
            project_id=default_project(hub.engine), routine_name="nightly", scope_slug="blizzard", mode="dry_run"
        ),
    )
    response = hub.client.get(f"/api/fleet/chunks/ch_{item.ref}/garden/proposals/page", params={"cursor": "invalid!"})
    assert response.json()["detail"] == "malformed cursor"
    _observe(
        hub.app.openapi(),
        response,
        "get",
        "/api/fleet/chunks/{chunk_id}/garden/proposals/page",
        MalformedCursorProblem,
        422,
    )

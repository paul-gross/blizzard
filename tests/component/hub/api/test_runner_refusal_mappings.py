"""Runner-bound refusals retain their details and publish their problem schemas."""

from pathlib import Path

import pytest
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient
from starlette.exceptions import HTTPException

from blizzard.hub.api.hub_level import hub_services
from blizzard.hub.api.problem_exceptions import problem_http_exception
from blizzard.hub.store.internal.graph_store import GraphStore
from blizzard.wire import problem
from blizzard.wire.route import PinnedGraphMissingProblem
from tests.support.hub_request_auth import bearer
from tests.support.refusal_observation import RefusalObservation, refusal_mismatches
from tests.support.scaffolding import build_hub, seed_runner


def test_runner_registry_refusals_and_declarations(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    token = seed_runner(hub, "runner-a")
    seed_runner(hub, "runner-b")
    path = "/api/fleet/runners/runner-b"
    response = hub.client.get(path, headers=bearer(token))
    assert response.status_code == 403
    assert response.headers["content-type"] == "application/problem+json"
    assert response.json()["type"] == problem.RUNNER_OWNERSHIP
    assert response.json()["detail"] == "token belongs to runner 'runner-a', not 'runner-b'"
    assert response.json()["instance"] == path

    spec = hub.client.get("/openapi.json").json()
    for route, code, model in (
        ("/api/fleet/runners/{runner_id}", "403", "RunnerOwnershipProblem"),
        ("/api/fleet/runners/{runner_id}", "409", "RunnerNotRegisteredProblem"),
        ("/api/fleet/chunks/{chunk_id}/findings", "404", "NoAnsweredProposalProblem"),
        ("/api/fleet/chunks/{chunk_id}/findings/{finding_id}", "404", "FindingNotInChunkProblem"),
        ("/api/fleet/chunks/{chunk_id}/garden/proposals", "503", "HubStoreUnavailableProblem"),
    ):
        schema = spec["paths"][route]["get"]["responses"][code]["content"]["application/problem+json"]["schema"]
        assert model in str(schema)


def test_runner_never_connected_problem(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    response = hub.client.get("/api/fleet/runners/ghost")
    assert response.status_code == 409
    assert response.headers["content-type"] == "application/problem+json"
    assert response.json()["type"] == problem.RUNNER_NOT_REGISTERED
    assert response.json()["detail"] == "runner ghost has not registered"


def test_unwired_hub_dependency_is_scoped_to_runner_reach() -> None:
    app = FastAPI()
    app.add_exception_handler(HTTPException, problem_http_exception)

    @app.get("/api/fleet/identity", dependencies=[Depends(hub_services)])
    @app.get("/unrelated", dependencies=[Depends(hub_services)])
    def endpoint() -> None:
        pass

    client = TestClient(app)
    response = client.get("/api/fleet/identity?secret=not-in-instance")
    assert response.status_code == 503
    assert response.headers["content-type"] == "application/problem+json"
    assert response.json()["type"] == problem.HUB_STORE_UNAVAILABLE
    assert response.json()["instance"] == "/api/fleet/identity"
    assert response.json()["detail"] == "hub store not wired — start via `blizzard hub host`"
    unrelated = client.get("/unrelated")
    assert unrelated.json() == {"detail": response.json()["detail"]}
    assert unrelated.headers["content-type"] == "application/json"


@pytest.mark.parametrize("operation", ["envelope", "hub-advance", "routes", "completions", "decisions"])
def test_missing_pinned_graph_refusal_from_runner_operations(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, operation: str
) -> None:
    hub = build_hub(tmp_path)
    token = seed_runner(hub, "runner-a")
    created = hub.client.post("/api/chunks", json={"tokens": ["default:1"]})
    assert created.status_code == 201, created.text
    chunk_id = str(created.json()["chunk_id"])

    def missing_graph(self: GraphStore, graph_id: str) -> None:
        return None

    monkeypatch.setattr(GraphStore, "get", missing_graph)
    path = f"/api/fleet/chunks/{chunk_id}/{operation}"
    if operation == "envelope":
        response = hub.client.get(path, headers=bearer(token))
    else:
        if operation == "routes":
            path = "/api/fleet/routes"
            body: dict[str, object] = {"chunk_id": chunk_id, "workspace_id": "w1", "environment_ids": []}
        else:
            body = {"epoch": 1, "from_node_id": "nd_1", "choice": "done"}
        response = hub.client.post(path, headers=bearer(token), json=body)

    assert response.status_code == 500, response.text
    assert response.headers["content-type"] == "application/problem+json"
    assert response.json()["type"] == problem.PINNED_GRAPH_MISSING
    assert response.json()["detail"] == "chunk's pinned graph is missing"
    assert response.json()["instance"] == path
    assert hub.app is not None
    method = "get" if operation == "envelope" else "post"
    template = "/api/fleet/routes" if operation == "routes" else f"/api/fleet/chunks/{{chunk_id}}/{operation}"
    observation = RefusalObservation(
        method,
        template,
        response.status_code,
        response.headers["content-type"],
        PinnedGraphMissingProblem,
        response.json(),
    )
    assert refusal_mismatches(hub.app.openapi(), observation) == []

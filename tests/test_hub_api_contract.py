"""Hub API contract surface (component tier) — the P6 route shapes.

The walking-skeleton routes are 501 stubs, but their *shapes* are the build
contract both tracks implement against: they must exist, appear in the committed
OpenAPI schema (so the generated TS client carries them), and reject with 501 —
not 404 (missing) and not 422 (a body-validation failure masking the stub). Sending
schema-valid bodies is what distinguishes "wired but unimplemented" from "the model
is wrong".
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from blizzard.hub.app import create_app_for_export

pytestmark = pytest.mark.component

_NEW_PATHS = [
    "/api/graphs",
    "/api/chunks",
    "/api/chunks/{chunk_id}",
    "/api/chunks/{chunk_id}/envelope",
    "/api/chunks/{chunk_id}/completions",
    "/api/chunks/{chunk_id}/pm-item",
    "/api/queue/peek",
    "/api/routes",
]


@pytest.fixture
def client() -> TestClient:
    return TestClient(create_app_for_export())


def test_all_new_paths_are_in_the_openapi_schema(client: TestClient) -> None:
    paths = create_app_for_export().openapi()["paths"]
    for path in _NEW_PATHS:
        assert path in paths, f"{path} missing from OpenAPI schema"


def test_get_routes_stub_501(client: TestClient) -> None:
    assert client.get("/api/chunks").status_code == 501
    assert client.get("/api/queue/peek").status_code == 501
    assert client.get("/api/chunks/ch_x").status_code == 501
    assert client.get("/api/chunks/ch_x/envelope").status_code == 501
    assert client.get("/api/chunks/ch_x/pm-item").status_code == 501


def test_post_graphs_with_valid_body_stub_501(client: TestClient) -> None:
    assert client.post("/api/graphs", json={"definition_yaml": "name: t"}).status_code == 501


def test_post_chunks_with_valid_body_stub_501(client: TestClient) -> None:
    body = {"pointers": [{"provider": "github", "url": "https://x/issues/1"}]}
    assert client.post("/api/chunks", json=body).status_code == 501


def test_post_routes_with_valid_body_stub_501(client: TestClient) -> None:
    body = {
        "chunk_id": "ch_x",
        "runner_id": "r1",
        "workspace_id": "w1",
        "environment_ids": ["alpha"],
    }
    assert client.post("/api/routes", json=body).status_code == 501


def test_post_completions_with_valid_body_stub_501(client: TestClient) -> None:
    body = {"choice": "pass", "epoch": 1, "runner_id": "r1", "from_node_id": "nd_build"}
    assert client.post("/api/chunks/ch_x/completions", json=body).status_code == 501


def test_missing_body_is_422_not_501(client: TestClient) -> None:
    # A body-less POST to a body-taking route fails validation before the stub —
    # proof the route validates against the wire model, not that the model is loose.
    assert client.post("/api/routes", json={}).status_code == 422

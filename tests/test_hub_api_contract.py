"""Hub API contract surface (component tier) — the P6 route shapes.

Pins the surface: the walking-skeleton routes exist, appear in the committed OpenAPI
schema, and a wired route rejects a malformed body with 422 rather than accepting a
loose one. Behavioral coverage of each route lives in the per-feature component
tests."""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from tests.config_inputs import export_hub_app
from tests.support import FakeWorkSource, build_hub, pointer_token

pytestmark = pytest.mark.component

_NEW_PATHS = [
    "/api/graphs",
    "/api/chunks",
    "/api/chunk-counts",
    "/api/chunks/{chunk_id}",
    "/api/chunks/{chunk_id}/work-items",
    "/api/queue",
    "/api/chunks/{chunk_id}/group",
    "/api/runners",
    "/api/runners/{runner_id}/pause",
    "/api/runners/{runner_id}/resume",
    "/api/runners/{runner_id}/enrollments",
    "/api/spend",
    # The runner-authenticated fleet router — mounted once with
    # `require_runner_principal` at router level.
    "/api/fleet/queue/peek",
    "/api/fleet/chunk-statuses",
    "/api/fleet/chunks/{chunk_id}",
    "/api/fleet/chunks/{chunk_id}/work-items",
    "/api/fleet/chunks/{chunk_id}/pause",
    "/api/fleet/chunks/{chunk_id}/resume",
    "/api/fleet/chunks/{chunk_id}/envelope",
    "/api/fleet/chunks/{chunk_id}/completions",
    "/api/fleet/chunks/{chunk_id}/decisions",
    "/api/fleet/questions/{question_id}",
    "/api/fleet/events",
    "/api/fleet/routes",
    "/api/fleet/runners",
    "/api/fleet/runners/{runner_id}",
    "/api/fleet/runners/{runner_id}/heartbeats",
    "/api/fleet/chunks/{chunk_id}/hub-advance",
]

# The work-source rename's deprecated HTTP aliases — `(canonical, alias)`.
_DEPRECATED_ALIAS_PAIRS = [
    ("/api/chunks/{chunk_id}/work-items", "/api/chunks/{chunk_id}/pm-items"),
    ("/api/fleet/chunks/{chunk_id}/work-items", "/api/fleet/chunks/{chunk_id}/pm-items"),
]

_SANCTIONED_DEPRECATED_OPERATIONS = {f"GET {alias}" for _, alias in _DEPRECATED_ALIAS_PAIRS} | {
    "POST /api/graphs/{graph_id}/follow-latest",
    # The marker and delivery routes a hub node's `run:` script called, superseded by the node's action.
    "POST /api/chunks/{chunk_id}/hub-markers",
    "POST /api/chunks/{chunk_id}/garden-delivery",
    "POST /api/chunks/{chunk_id}/review-findings-delivery",
    # The tenant-scoped graph sync, superseded by the hub-level one over every open tenant.
    "POST /api/graphs/sync",
    # The users routes, superseded by the members routes of the request's tenant.
    "GET /api/users",
    "POST /api/users/{user_id}/role",
    # The un-nested gardening verbs, superseded by the same verbs under `/api/projects/{project}`.
    "POST /api/scopes",
    "GET /api/scopes/{slug}",
    "PATCH /api/scopes/{slug}",
    "GET /api/scopes/{slug}/routines",
    "POST /api/scopes/{slug}/retire",
    "POST /api/scopes/{slug}/enable",
    "POST /api/routines",
    "POST /api/garden-proposals",
}


def test_all_new_paths_are_in_the_openapi_schema() -> None:
    paths = export_hub_app().openapi()["paths"]
    for path in _NEW_PATHS:
        assert path in paths, f"{path} missing from OpenAPI schema"


def test_events_stream_excluded_from_openapi() -> None:
    assert "/api/events/stream" not in export_hub_app().openapi()["paths"]


def test_the_only_deprecated_operations_are_the_sanctioned_aliases() -> None:
    # An allow-list, not a relaxation — a *new* deprecated route fails until named.
    # A path item maps HTTP methods to operation objects, plus a shared ``parameters`` list.
    paths: dict[str, dict[str, dict[str, object] | list[object]]] = export_hub_app().openapi()["paths"]
    deprecated = {
        f"{method.upper()} {path}"
        for path, operations in paths.items()
        for method, operation in operations.items()
        if isinstance(operation, dict) and operation.get("deprecated")
    }
    assert deprecated == _SANCTIONED_DEPRECATED_OPERATIONS, (
        f"unexpected deprecated operations: {sorted(deprecated - _SANCTIONED_DEPRECATED_OPERATIONS)}; "
        f"unexpectedly undeprecated: {sorted(_SANCTIONED_DEPRECATED_OPERATIONS - deprecated)}"
    )


def test_each_deprecated_alias_returns_the_same_view_as_its_canonical_route(tmp_path: Path) -> None:
    """One handler, two routes — the alias is not a second implementation to
    drift. Driven against a real chunk carrying a work ref, so the assertion compares a
    populated view rather than two identical empty lists."""
    hub = build_hub(tmp_path, work_sources={"widget": FakeWorkSource(name="widget", body="the issue body")})
    chunk_id = hub.client.post(
        "/api/chunks", json={"tokens": [pointer_token({"source": "widget", "ref": "42"})]}
    ).json()["chunk_id"]

    for canonical, alias in _DEPRECATED_ALIAS_PAIRS:
        canonical_resp = hub.client.get(canonical.format(chunk_id=chunk_id))
        alias_resp = hub.client.get(alias.format(chunk_id=chunk_id))
        assert canonical_resp.status_code == 200, canonical
        assert alias_resp.status_code == 200, alias
        assert alias_resp.json() == canonical_resp.json(), f"{alias} drifted from {canonical}"
        assert canonical_resp.json()["items"][0]["body"] == "the issue body", "the fixture read nothing to compare"


def test_store_free_app_reports_fleet_routes_unwired() -> None:
    # Built without a store, the fleet routes report the store is unwired (503),
    # never a 500 — the dependency guards before the handler runs.
    client = TestClient(export_hub_app())
    assert client.get("/api/chunks").status_code == 503
    assert client.get("/api/queue").status_code == 503


def test_missing_body_is_422_on_a_wired_route(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    # A body-less POST to a body-taking route fails validation against the wire model.
    assert hub.client.post("/api/fleet/routes", json={}).status_code == 422
    assert hub.client.post("/api/chunks", json={}).status_code == 422

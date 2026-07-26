"""Hub API contract surface (component tier) — the P6 route shapes.

The walking-skeleton routes must exist, appear in the committed OpenAPI schema (so
the generated TS client carries them), and validate their bodies against the wire
models. Behavioural coverage of each route lives in the per-feature component tests
(``test_ingest_and_queue``, ``test_route_claim``, ``test_completion_apply``,
``test_delivery_loop``); this file pins the surface: schema presence, and that a
wired route rejects a malformed body with 422 rather than accepting a loose one.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from blizzard.hub.app import create_app_for_export
from tests.support import FakeWorkSource, build_hub, pointer_token

pytestmark = pytest.mark.component

_NEW_PATHS = [
    "/api/graphs",
    "/api/chunks",
    "/api/chunks/{chunk_id}",
    "/api/chunks/{chunk_id}/work-items",
    "/api/queue",
    "/api/chunks/{chunk_id}/group",
    "/api/runners",
    "/api/runners/{runner_id}/pause",
    "/api/runners/{runner_id}/resume",
    "/api/runners/{runner_id}/enrollments",
    "/api/spend",
    # The runner-authenticated fleet router (issue #87) — mounted once with
    # `require_runner_principal` at router level.
    "/api/fleet/queue/peek",
    "/api/fleet/chunks/{chunk_id}",
    "/api/fleet/chunks/{chunk_id}/work-items",
    "/api/fleet/chunks/{chunk_id}/envelope",
    "/api/fleet/chunks/{chunk_id}/completions",
    "/api/fleet/chunks/{chunk_id}/decisions",
    "/api/fleet/chunks/{chunk_id}/leases",
    "/api/fleet/chunks/{chunk_id}/escalations",
    "/api/fleet/questions/{question_id}",
    "/api/fleet/events",
    "/api/fleet/routes",
    "/api/fleet/runners",
    "/api/fleet/runners/{runner_id}",
    "/api/fleet/runners/{runner_id}/heartbeats",
    "/api/fleet/chunks/{chunk_id}/hub-advance",
]

# The work-source rename's deprecated HTTP aliases (issue #55) — `(canonical, alias)`.
# The runner's own `/api/chunks/{id}/pm-items` proxy alias is the same decision on the
# other daemon; it is pinned in `test_work_items_proxy.py`, against a runner app.
_DEPRECATED_ALIAS_PAIRS = [
    ("/api/chunks/{chunk_id}/work-items", "/api/chunks/{chunk_id}/pm-items"),
    ("/api/fleet/chunks/{chunk_id}/work-items", "/api/fleet/chunks/{chunk_id}/pm-items"),
]

_SANCTIONED_DEPRECATED_OPERATIONS = {f"GET {alias}" for _, alias in _DEPRECATED_ALIAS_PAIRS}


def test_all_new_paths_are_in_the_openapi_schema() -> None:
    paths = create_app_for_export().openapi()["paths"]
    for path in _NEW_PATHS:
        assert path in paths, f"{path} missing from OpenAPI schema"


def test_events_stream_excluded_from_openapi() -> None:
    assert "/api/events/stream" not in create_app_for_export().openapi()["paths"]


def test_the_only_deprecated_operations_are_the_sanctioned_aliases() -> None:
    # Issue #105 removed the last deprecated-alias routes, and the schema went back to
    # exactly one route per operation. Issue #55's work-source rename adds these two back
    # deliberately: the HTTP surface is reachable by out-of-tree callers, so `/pm-items`
    # stays as a deprecated alias onto the same handler. The assertion is an allow-list,
    # not a relaxation — a *new* deprecated route still fails here until it is named.
    paths = create_app_for_export().openapi()["paths"]
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
    """One handler, two routes (issue #55) — the alias is not a second implementation to
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
    client = TestClient(create_app_for_export())
    assert client.get("/api/chunks").status_code == 503
    assert client.get("/api/queue").status_code == 503


def test_missing_body_is_422_on_a_wired_route(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    # A body-less POST to a body-taking route fails validation against the wire model.
    assert hub.client.post("/api/fleet/routes", json={}).status_code == 422
    assert hub.client.post("/api/chunks", json={}).status_code == 422

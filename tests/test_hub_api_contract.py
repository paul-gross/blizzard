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
from tests.support import build_hub

pytestmark = pytest.mark.component

_NEW_PATHS = [
    "/api/graphs",
    "/api/chunks",
    "/api/chunks/{chunk_id}",
    "/api/chunks/{chunk_id}/envelope",
    "/api/chunks/{chunk_id}/completions",
    "/api/chunks/{chunk_id}/pm-items",
    "/api/queue/peek",
    "/api/queue/reorder",
    "/api/chunks/{chunk_id}/group",
    "/api/routes",
    "/api/runners",
    "/api/runners/{runner_id}",
    "/api/runners/{runner_id}/heartbeats",
    "/api/runners/{runner_id}/pause",
    "/api/runners/{runner_id}/resume",
]


def test_all_new_paths_are_in_the_openapi_schema() -> None:
    paths = create_app_for_export().openapi()["paths"]
    for path in _NEW_PATHS:
        assert path in paths, f"{path} missing from OpenAPI schema"


def test_events_stream_excluded_from_openapi() -> None:
    assert "/api/events/stream" not in create_app_for_export().openapi()["paths"]


def test_store_free_app_reports_fleet_routes_unwired() -> None:
    # Built without a store, the fleet routes report the store is unwired (503),
    # never a 500 — the dependency guards before the handler runs.
    client = TestClient(create_app_for_export())
    assert client.get("/api/chunks").status_code == 503
    assert client.get("/api/queue/peek").status_code == 503


def test_missing_body_is_422_on_a_wired_route(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    # A body-less POST to a body-taking route fails validation against the wire model.
    assert hub.client.post("/api/routes", json={}).status_code == 422
    assert hub.client.post("/api/chunks", json={}).status_code == 422

"""``GET``/``PUT /api/queue`` and the deprecated ``peek``/``reorder`` aliases (issue #104),
component tier.

``GET /api/queue`` is the same hub-ordered ready-queue view ``GET /queue/peek`` always
served; ``PUT /api/queue`` is the new idempotent whole-order replacement
(``bzh:domain-takes-objects`` — the controller resolves every named id against the ready
set and validates before the domain ever sees a ``Chunk``). The two deprecated routes
must keep working byte-identically and must carry the ``Deprecation``/``Link`` headers
and ``deprecated: true`` in the OpenAPI operation; a runner bearer token must still be
rejected on every route in this router.
"""

from __future__ import annotations

from datetime import timedelta
from pathlib import Path

import pytest

from tests.support import HubHarness, build_hub, pointer_token

pytestmark = pytest.mark.component


def _ingest(hub: HubHarness, n: int) -> str:
    pointer = {"source": "default", "ref": str(n)}
    resp = hub.client.post("/api/chunks", json={"tokens": [pointer_token(pointer)]})
    assert resp.status_code == 201, resp.text
    chunk_id = resp.json()["chunk_id"]
    assert hub.client.post(f"/api/chunks/{chunk_id}/promote").status_code == 202
    hub.clock.advance(timedelta(seconds=1))  # distinct minted_at → deterministic FIFO
    return chunk_id


def _ids(entries: list[dict]) -> list[str]:
    return [e["chunk_id"] for e in entries]


# --- GET /api/queue ---------------------------------------------------------


def test_get_queue_returns_the_same_view_peek_did(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    a, b, c = _ingest(hub, 1), _ingest(hub, 2), _ingest(hub, 3)
    resp = hub.client.get("/api/queue")
    assert resp.status_code == 200, resp.text
    assert _ids(resp.json()["entries"]) == [a, b, c]
    assert resp.json() == hub.client.get("/api/queue/peek").json()


# --- PUT /api/queue — whole-order replace -----------------------------------


def test_put_queue_replaces_the_whole_order(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    a, b, c = _ingest(hub, 1), _ingest(hub, 2), _ingest(hub, 3)
    resp = hub.client.put("/api/queue", json={"chunk_ids": [c, a, b]})
    assert resp.status_code == 200, resp.text
    assert _ids(resp.json()["entries"]) == [c, a, b]
    assert _ids(hub.client.get("/api/queue").json()["entries"]) == [c, a, b]


def test_put_queue_is_idempotent(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    a, b, c = _ingest(hub, 1), _ingest(hub, 2), _ingest(hub, 3)
    order = {"chunk_ids": [b, c, a]}
    first = hub.client.put("/api/queue", json=order)
    second = hub.client.put("/api/queue", json=order)
    assert first.json() == second.json()
    assert _ids(second.json()["entries"]) == [b, c, a]


def test_put_queue_appends_unlisted_ready_chunks_after_the_named_ones(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    a, b, c = _ingest(hub, 1), _ingest(hub, 2), _ingest(hub, 3)
    # Only name b — a and c are unlisted and keep their relative FIFO order at the tail.
    resp = hub.client.put("/api/queue", json={"chunk_ids": [b]})
    assert resp.status_code == 200, resp.text
    assert _ids(resp.json()["entries"]) == [b, a, c]


def test_put_queue_naming_a_non_ready_chunk_is_409(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    a, b = _ingest(hub, 1), _ingest(hub, 2)
    claim = hub.client.post(
        "/api/fleet/routes",
        json={"chunk_id": a, "runner_id": "r1", "workspace_id": "w1", "environment_ids": ["e"]},
    )
    assert claim.status_code == 201, claim.text
    resp = hub.client.put("/api/queue", json={"chunk_ids": [a, b]})
    assert resp.status_code == 409
    assert a in resp.json()["detail"]
    # Rejected wholesale — b's ready order is untouched by the failed attempt.
    assert _ids(hub.client.get("/api/queue").json()["entries"]) == [b]


def test_put_queue_naming_an_unknown_chunk_is_409(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    _ingest(hub, 1)
    resp = hub.client.put("/api/queue", json={"chunk_ids": ["ch_nope"]})
    assert resp.status_code == 409
    assert "ch_nope" in resp.json()["detail"]


def test_put_queue_duplicate_ids_is_422(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    a = _ingest(hub, 1)
    resp = hub.client.put("/api/queue", json={"chunk_ids": [a, a]})
    assert resp.status_code == 422


# --- Deprecated aliases: still work, carry headers --------------------------


def test_peek_alias_still_works_and_carries_deprecation_headers(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    a = _ingest(hub, 1)
    resp = hub.client.get("/api/queue/peek")
    assert resp.status_code == 200, resp.text
    assert _ids(resp.json()["entries"]) == [a]
    assert resp.headers["Deprecation"] == "true"
    assert resp.headers["Link"] == '</api/queue>; rel="successor-version"'


def test_reorder_alias_still_works_and_carries_deprecation_headers(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    a, b = _ingest(hub, 1), _ingest(hub, 2)
    resp = hub.client.post("/api/queue/reorder", json={"chunk_id": b, "position": 0})
    assert resp.status_code == 200, resp.text
    assert _ids(resp.json()["entries"]) == [b, a]
    assert resp.headers["Deprecation"] == "true"
    assert resp.headers["Link"] == '</api/queue>; rel="successor-version"'


def test_deprecated_routes_are_marked_in_the_openapi_schema() -> None:
    from blizzard.hub.app import create_app_for_export

    schema = create_app_for_export().openapi()
    assert schema["paths"]["/api/queue/peek"]["get"]["deprecated"] is True
    assert schema["paths"]["/api/queue/reorder"]["post"]["deprecated"] is True
    assert "deprecated" not in schema["paths"]["/api/queue"]["get"]
    assert "deprecated" not in schema["paths"]["/api/queue"]["put"]


# --- Runner principal is still rejected on every route in this router -------


def test_runner_bearer_token_is_rejected_on_get_and_put_queue(tmp_path: Path) -> None:
    from blizzard.hub.config import RUNNER_AUTH_ENFORCE
    from tests.test_fleet_auth import _bearer, _seed_enrolled

    token = _seed_enrolled(tmp_path)
    hub = build_hub(tmp_path, runner_auth_mode=RUNNER_AUTH_ENFORCE)
    assert hub.client.get("/api/queue", headers=_bearer(token)).status_code == 403
    assert hub.client.put("/api/queue", json={"chunk_ids": []}, headers=_bearer(token)).status_code == 403
    assert hub.client.get("/api/queue/peek", headers=_bearer(token)).status_code == 403

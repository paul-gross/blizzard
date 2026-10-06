"""The fleet-router partition — structural runner-auth enforcement (component tier).

Proves the partition: a valid runner token is confined to the fleet router
(authenticates a fleet verb, rejected on an operator verb) and every verb this phase
moved is gone from its old anonymous path."""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from tests.support import build_hub, seed_runner

pytestmark = pytest.mark.component


def _register(hub, runner_id: str = "runner-a", workspace_id: str = "ws-a") -> None:  # type: ignore[no-untyped-def]
    resp = hub.client.post("/api/fleet/runners", json={"runner_id": runner_id, "workspace_id": workspace_id})
    assert resp.status_code == 201, resp.text


def _enroll(hub, runner_id: str = "runner-a") -> str:  # type: ignore[no-untyped-def]
    resp = hub.client.post(f"/api/runners/{runner_id}/enrollments")
    assert resp.status_code == 201, resp.text
    return str(resp.json()["token"])


def _seed_enrolled(tmp_path: Path, runner_id: str = "runner-a", workspace_id: str = "ws-a") -> str:
    """Add and register ``runner_id`` in the store over ``tmp_path``; return its token. The caller
    builds its own hub over the same store, as a restarted hub reopens it."""
    return seed_runner(build_hub(tmp_path), runner_id, workspace_id=workspace_id)


def _bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


# A valid runner token succeeds on a fleet verb
# --------------------------------------------------------------------------- #


def test_valid_runner_token_succeeds_on_a_fleet_verb(tmp_path: Path) -> None:
    token = _seed_enrolled(tmp_path)
    hub = build_hub(tmp_path)

    resp = hub.client.get("/api/fleet/queue/peek", headers=_bearer(token))
    assert resp.status_code == 200


@pytest.mark.parametrize("auth_mode", ["none", "oauth"])
def test_missing_token_is_rejected_on_a_fleet_verb_under_every_configuration(tmp_path: Path, auth_mode: str) -> None:
    hub = build_hub(tmp_path, auth_mode=auth_mode)
    assert hub.app is not None
    resp = TestClient(hub.app).get("/api/fleet/queue/peek")
    assert (resp.status_code, resp.json()) == (401, {"detail": "missing or malformed Authorization header"})


# The same token is rejected on an operator verb — not anonymous-plus-credential
# --------------------------------------------------------------------------- #


def test_valid_runner_token_is_rejected_on_ingest(tmp_path: Path) -> None:
    token = _seed_enrolled(tmp_path)
    hub = build_hub(tmp_path)

    resp = hub.client.post("/api/chunks", json={"tokens": ["default:1"]}, headers=_bearer(token))
    assert resp.status_code == 403


def test_valid_runner_token_is_rejected_on_queue_replace(tmp_path: Path) -> None:
    token = _seed_enrolled(tmp_path)
    hub = build_hub(tmp_path)

    resp = hub.client.put("/api/queue", json={"chunk_ids": []}, headers=_bearer(token))
    assert resp.status_code == 403


def test_valid_runner_token_is_rejected_on_pause_resume(tmp_path: Path) -> None:
    token = _seed_enrolled(tmp_path)
    hub = build_hub(tmp_path)

    paused = hub.client.post("/api/runners/runner-a/pause", json={"by": "op"}, headers=_bearer(token))
    assert paused.status_code == 403
    resumed = hub.client.post("/api/runners/runner-a/resume", json={"by": "op"}, headers=_bearer(token))
    assert resumed.status_code == 403


# Operator verbs stay accessible with no credential
# --------------------------------------------------------------------------- #


def test_operator_verbs_are_accessible_with_no_credential(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    assert hub.client.post("/api/chunks", json={"tokens": ["default:1"]}).status_code == 201
    assert hub.client.get("/api/spend", params={"since": "1970-01-01T00:00:00+00:00"}).status_code == 200


# A fleet write is the token's runner's, whatever its body declares
# --------------------------------------------------------------------------- #


def test_a_body_runner_id_naming_another_runner_is_ignored_on_a_fleet_write(tmp_path: Path) -> None:
    token = _seed_enrolled(tmp_path, "runner-a", "ws-a")
    hub = build_hub(tmp_path)
    chunk_id = hub.client.post("/api/chunks", json={"tokens": ["default:1"]}).json()["chunk_id"]

    resp = hub.client.post(
        "/api/fleet/events",
        json={
            "runner_id": "runner-b",
            "facts": [{"seq": 1, "kind": "lease.minted", "payload": {"chunk_id": chunk_id, "epoch": 1}}],
        },
        headers=_bearer(token),
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["runner_id"] == "runner-a"
    assert hub.services.registry.get_runner("runner-b") is None


# A path naming another runner is refused, whatever the token
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    ("method", "path"), [("post", "/api/fleet/runners/runner-b/heartbeats"), ("get", "/api/fleet/runners/runner-b")]
)
def test_a_runner_verb_on_a_path_naming_another_runner_is_refused_403(tmp_path: Path, method: str, path: str) -> None:
    hub = build_hub(tmp_path)
    token = seed_runner(hub, "runner-a")
    seed_runner(hub, "runner-b")

    resp = getattr(hub.client, method)(path, headers=_bearer(token))

    assert resp.status_code == 403, resp.text
    assert resp.json() == {"detail": "token belongs to runner 'runner-a', not 'runner-b'"}


# No fleet route reachable at its old anonymous path
# --------------------------------------------------------------------------- #


def test_moved_write_verbs_404_or_405_at_their_old_path(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    for method, path in [
        ("post", "/api/routes"),
        ("post", "/api/chunks/ch_x/completions"),
        ("post", "/api/chunks/ch_x/decisions"),
        ("post", "/api/chunks/ch_x/leases"),
        ("post", "/api/chunks/ch_x/escalations"),
        ("post", "/api/events"),
        # `POST /api/runners` is the operator's add verb; registration is `POST /api/fleet/runners`.
        ("post", "/api/runners/ghost/heartbeats"),
        ("post", "/api/chunks/ch_x/hub-advance"),
    ]:
        resp = getattr(hub.client, method)(path, json={})
        assert resp.status_code in (404, 405), f"{method.upper()} {path} still reachable: {resp.status_code}"


def test_moved_read_verbs_are_gone_from_the_route_inventory(tmp_path: Path) -> None:
    """The moved GET reads no longer resolve as operator API routes, and the
    fleet-side counterparts are present. Asserted against the OpenAPI inventory,
    since what a dead GET serves depends on whether the SPA bundle is built."""
    hub = build_hub(tmp_path)
    app = hub.client.app
    assert isinstance(app, FastAPI)
    paths = app.openapi()["paths"]
    for old, new in [
        ("/api/chunks/{chunk_id}/envelope", "/api/fleet/chunks/{chunk_id}/envelope"),
        ("/api/questions/{question_id}", "/api/fleet/questions/{question_id}"),
    ]:
        assert "get" not in paths.get(old, {}), f"GET {old} still resolves as an API route"
        assert "get" in paths.get(new, {}), f"GET {new} missing from the fleet router"
    assert "get" in paths.get("/api/fleet/runners/{runner_id}", {}), "fleet runner pull read missing"

"""The fleet-router partition — structural runner-auth enforcement (component tier,
issue #87).

``tests/test_hub_auth.py`` covers ``assert_owns`` in isolation (unit tier) and
``tests/test_runner_enrollment.py`` covers ``require_runner_principal`` at the one
route Phase 1 landed it on. This file proves the Phase 3 partition itself: a valid
runner token is confined to the fleet router — it authenticates a fleet verb and is
*rejected* on an operator verb, not silently treated as anonymous-plus-credential —
and every verb this phase moved is gone from its old anonymous path.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi import FastAPI

from blizzard.hub.config import RUNNER_AUTH_ENFORCE
from tests.support import build_hub

pytestmark = pytest.mark.component


def _register(hub, runner_id: str = "runner-a", workspace_id: str = "ws-a") -> None:  # type: ignore[no-untyped-def]
    resp = hub.client.post("/api/fleet/runners", json={"runner_id": runner_id, "workspace_id": workspace_id})
    assert resp.status_code == 201, resp.text


def _enroll(hub, runner_id: str = "runner-a") -> str:  # type: ignore[no-untyped-def]
    resp = hub.client.post(f"/api/runners/{runner_id}/enrollments")
    assert resp.status_code == 201, resp.text
    return str(resp.json()["token"])


def _seed_enrolled(tmp_path: Path, runner_id: str = "runner-a", workspace_id: str = "ws-a") -> str:
    """Register + enroll ``runner_id`` under a throwaway ``warn`` hub; return its token —
    the same two-hub-instances-over-one-store shape ``test_runner_enrollment.py`` uses,
    since registration is itself auth-checked."""
    warn_hub = build_hub(tmp_path)
    _register(warn_hub, runner_id=runner_id, workspace_id=workspace_id)
    return _enroll(warn_hub, runner_id)


def _bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


# --------------------------------------------------------------------------- #
# A valid runner token succeeds on a fleet verb
# --------------------------------------------------------------------------- #


def test_valid_runner_token_succeeds_on_a_fleet_verb(tmp_path: Path) -> None:
    token = _seed_enrolled(tmp_path)
    hub = build_hub(tmp_path, runner_auth_mode=RUNNER_AUTH_ENFORCE)

    resp = hub.client.get("/api/fleet/queue/peek", headers=_bearer(token))
    assert resp.status_code == 200


def test_missing_token_is_rejected_on_a_fleet_verb_under_enforce(tmp_path: Path) -> None:
    hub = build_hub(tmp_path, runner_auth_mode=RUNNER_AUTH_ENFORCE)
    assert hub.client.get("/api/fleet/queue/peek").status_code == 401


# --------------------------------------------------------------------------- #
# The same token is rejected on an operator verb — not anonymous-plus-credential
# --------------------------------------------------------------------------- #


def test_valid_runner_token_is_rejected_on_ingest(tmp_path: Path) -> None:
    token = _seed_enrolled(tmp_path)
    hub = build_hub(tmp_path, runner_auth_mode=RUNNER_AUTH_ENFORCE)

    resp = hub.client.post("/api/chunks", json={"tokens": ["default:1"]}, headers=_bearer(token))
    assert resp.status_code == 403


def test_valid_runner_token_is_rejected_on_queue_replace(tmp_path: Path) -> None:
    token = _seed_enrolled(tmp_path)
    hub = build_hub(tmp_path, runner_auth_mode=RUNNER_AUTH_ENFORCE)

    resp = hub.client.put("/api/queue", json={"chunk_ids": []}, headers=_bearer(token))
    assert resp.status_code == 403


def test_valid_runner_token_is_rejected_on_pause_resume(tmp_path: Path) -> None:
    token = _seed_enrolled(tmp_path)  # registers + enrolls "runner-a" under a throwaway warn hub
    hub = build_hub(tmp_path, runner_auth_mode=RUNNER_AUTH_ENFORCE)

    paused = hub.client.post("/api/runners/runner-a/pause", json={"by": "op"}, headers=_bearer(token))
    assert paused.status_code == 403
    resumed = hub.client.post("/api/runners/runner-a/resume", json={"by": "op"}, headers=_bearer(token))
    assert resumed.status_code == 403


def test_valid_runner_token_under_warn_is_logged_and_proceeds_on_an_operator_verb(tmp_path: Path) -> None:
    """``warn`` (the default) is a rollout brake, not a partition: the token is
    warn-logged, not rejected, so an already-deployed anonymous fleet keeps working
    unchanged until the operator flips to ``enforce``."""
    token = _seed_enrolled(tmp_path)
    hub = build_hub(tmp_path)  # warn, the default

    resp = hub.client.post("/api/chunks", json={"tokens": ["default:1"]}, headers=_bearer(token))
    assert resp.status_code in (201, 409)  # not 403 — a normal ingest outcome


# --------------------------------------------------------------------------- #
# Operator verbs stay accessible with no credential
# --------------------------------------------------------------------------- #


def test_operator_verbs_are_accessible_with_no_credential(tmp_path: Path) -> None:
    hub = build_hub(tmp_path, runner_auth_mode=RUNNER_AUTH_ENFORCE)
    assert hub.client.post("/api/chunks", json={"tokens": ["default:1"]}).status_code == 201
    assert hub.client.get("/api/spend", params={"since": "1970-01-01T00:00:00+00:00"}).status_code == 200


# --------------------------------------------------------------------------- #
# Declared-runner_id confinement on a fleet write
# --------------------------------------------------------------------------- #


def test_wrong_runner_id_is_rejected_on_a_fleet_write(tmp_path: Path) -> None:
    token = _seed_enrolled(tmp_path, "runner-a", "ws-a")
    hub = build_hub(tmp_path, runner_auth_mode=RUNNER_AUTH_ENFORCE)
    chunk_id = hub.client.post("/api/chunks", json={"tokens": ["default:1"]}).json()["chunk_id"]

    resp = hub.client.post(
        f"/api/fleet/chunks/{chunk_id}/leases",
        json={"epoch": 1, "runner_id": "runner-b"},
        headers=_bearer(token),
    )
    assert resp.status_code == 403


# --------------------------------------------------------------------------- #
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
        ("post", "/api/runners"),
        ("post", "/api/runners/ghost/heartbeats"),
        ("post", "/api/chunks/ch_x/hub-advance"),
    ]:
        resp = getattr(hub.client, method)(path, json={})
        assert resp.status_code in (404, 405), f"{method.upper()} {path} still reachable: {resp.status_code}"


def test_moved_read_verbs_are_gone_from_the_route_inventory(tmp_path: Path) -> None:
    """The moved GET reads (envelope, the runner's answer poll) no longer resolve as
    operator API routes: their old path templates are absent from the app's OpenAPI
    path inventory, and the fleet-side counterparts are present. Asserted against the
    inventory rather than over HTTP because what a dead GET path serves depends on
    whether the SPA bundle is built (catch-all HTML) or not (a JSON 404).

    ``/api/runners/{runner_id}`` is excluded from this table (issue #104, S5): it was
    reintroduced as the operator's own detail read (``reject_runner_principal``-gated,
    reusing ``runner_view``) — see ``test_runners_api.py`` — coexisting with the
    runner-authenticated ``/api/fleet/runners/{runner_id}`` (the runner's own pull
    read) at a different prefix."""
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

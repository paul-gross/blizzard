"""Session-cookie/bearer resolution + ``require(<permission>)`` gating under
``auth.mode = "oauth"`` (component tier, issue #91).

``auth.mode = "none"`` (the default) is exercised implicitly by the whole rest of the
suite — every pre-#91 test builds a hub with no ``auth_mode`` override and keeps
passing unchanged. This file is the ``oauth``-mode half of the AC: ``require()``
grants/denies per the static role map, a ``guest`` reaches only ``GET /api/me`` (and,
per the route table, the not-yet-landed login surface), a ``guest`` is refused on the
SSE stream, an expired/absent session is 401, and the attribution overwrite lands the
*session* identity even under real gating (not just the ``none``-mode implicit
"operator" case ``test_ask_answer.py``/``test_decisions_api.py`` already cover).
"""

from __future__ import annotations

from datetime import timedelta
from pathlib import Path

import pytest

from blizzard.auth_core import Role
from tests.support import build_hub, pointer_token, seed_session, seed_user

pytestmark = pytest.mark.component

_POINTER = {"source": "default", "ref": "7"}

_GRAPH_YAML = """
name: default-delivery
entry: build
nodes:
  build:
    executor: runner
    prompt: |
      Build the change.
    judgement:
      prompt: |
        Assess the build.
      choices:
        pass:
          description: Complete and green.
          to: done
"""


def _cookie(token: str) -> dict[str, str]:
    return {"Cookie": f"bz_session={token}"}


def _bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


# --- No session presented ------------------------------------------------------


def test_no_session_is_401_on_a_permission_gated_route(tmp_path: Path) -> None:
    hub = build_hub(tmp_path, auth_mode="oauth")
    resp = hub.client.get("/api/chunks")
    assert resp.status_code == 401


def test_no_session_is_401_on_the_me_route(tmp_path: Path) -> None:
    hub = build_hub(tmp_path, auth_mode="oauth")
    resp = hub.client.get("/api/me")
    assert resp.status_code == 401


# --- guest: only /api/me (and the not-yet-landed login/logout surface) --------


def test_guest_reaches_me_but_is_refused_a_fleet_view_read(tmp_path: Path) -> None:
    hub = build_hub(tmp_path, auth_mode="oauth")
    guest = seed_user(hub, username="newcomer", role=Role.GUEST)
    token = seed_session(hub, guest)

    me = hub.client.get("/api/me", headers=_cookie(token))
    assert me.status_code == 200, me.text
    assert me.json()["role"] == "guest"
    assert me.json()["permissions"] == []

    denied = hub.client.get("/api/chunks", headers=_cookie(token))
    assert denied.status_code == 403


def test_guest_is_refused_on_the_sse_stream(tmp_path: Path) -> None:
    hub = build_hub(tmp_path, auth_mode="oauth")
    guest = seed_user(hub, username="newcomer", role=Role.GUEST)
    token = seed_session(hub, guest)

    resp = hub.client.get("/api/events/stream", headers=_cookie(token))
    assert resp.status_code == 403


def test_guest_is_refused_ingest(tmp_path: Path) -> None:
    hub = build_hub(tmp_path, auth_mode="oauth")
    guest = seed_user(hub, username="newcomer", role=Role.GUEST)
    token = seed_session(hub, guest)

    resp = hub.client.post("/api/chunks", json={"tokens": ["default:1"]}, headers=_cookie(token))
    assert resp.status_code == 403


# --- contributor: the operating surface, denied admin-tier writes -------------


def test_contributor_grants_operating_permissions_denies_admin_tier(tmp_path: Path) -> None:
    hub = build_hub(tmp_path, auth_mode="oauth")
    contributor = seed_user(hub, username="cara", role=Role.CONTRIBUTOR)
    token = seed_session(hub, contributor)

    assert hub.client.get("/api/chunks", headers=_cookie(token)).status_code == 200
    assert (
        hub.client.post("/api/graphs", json={"definition_yaml": _GRAPH_YAML}, headers=_cookie(token)).status_code == 403
    )
    assert hub.client.post("/api/runners/ghost/pause", json={"by": "cara"}, headers=_cookie(token)).status_code == 403


def test_contributor_can_ingest_and_answer(tmp_path: Path) -> None:
    hub = build_hub(tmp_path, auth_mode="oauth")
    admin = seed_user(hub, username="root", role=Role.SUPERUSER)
    admin_token = seed_session(hub, admin)
    assert (
        hub.client.post("/api/graphs", json={"definition_yaml": _GRAPH_YAML}, headers=_cookie(admin_token)).status_code
        == 201
    )

    contributor = seed_user(hub, username="cara", role=Role.CONTRIBUTOR)
    token = seed_session(hub, contributor)
    ingested = hub.client.post("/api/chunks", json={"tokens": [pointer_token(_POINTER)]}, headers=_cookie(token))
    assert ingested.status_code == 201, ingested.text


# --- admin: the fleet-identity + graph-authoring + user-admin tier ------------


def test_admin_grants_runner_pause_and_graph_edit(tmp_path: Path) -> None:
    hub = build_hub(tmp_path, auth_mode="oauth")
    admin = seed_user(hub, username="ada", role=Role.ADMIN)
    token = seed_session(hub, admin)

    minted = hub.client.post("/api/graphs", json={"definition_yaml": _GRAPH_YAML}, headers=_cookie(token))
    assert minted.status_code == 201, minted.text

    paused = hub.client.post("/api/runners/ghost/pause", json={"by": "ada"}, headers=_cookie(token))
    assert paused.status_code == 404  # gate granted; the route's own 404 (unknown runner) fires next


# --- expired session -----------------------------------------------------------


def test_expired_session_is_401(tmp_path: Path) -> None:
    hub = build_hub(tmp_path, auth_mode="oauth")
    user = seed_user(hub, username="ada", role=Role.CONTRIBUTOR)
    token = seed_session(hub, user)

    hub.clock.advance(timedelta(days=2))  # past the default idle TTL
    resp = hub.client.get("/api/chunks", headers=_cookie(token))
    assert resp.status_code == 401


# --- bearer header path (the CLI's future transport, #96) ---------------------


def test_bearer_header_resolves_the_same_as_the_cookie(tmp_path: Path) -> None:
    hub = build_hub(tmp_path, auth_mode="oauth")
    user = seed_user(hub, username="ada", role=Role.CONTRIBUTOR)
    token = seed_session(hub, user)

    resp = hub.client.get("/api/chunks", headers=_bearer(token))
    assert resp.status_code == 200


# --- attribution overwrite under real gating -----------------------------------


def test_answer_attribution_lands_the_session_identity_not_the_spoofed_body(tmp_path: Path) -> None:
    hub = build_hub(tmp_path, auth_mode="oauth")
    admin = seed_user(hub, username="root", role=Role.SUPERUSER)
    admin_token = seed_session(hub, admin)
    assert (
        hub.client.post("/api/graphs", json={"definition_yaml": _GRAPH_YAML}, headers=_cookie(admin_token)).status_code
        == 201
    )
    chunk_id = hub.client.post(
        "/api/chunks", json={"tokens": [pointer_token(_POINTER)]}, headers=_cookie(admin_token)
    ).json()["chunk_id"]
    hub.client.post(f"/api/chunks/{chunk_id}/promote", headers=_cookie(admin_token))
    claim = hub.client.post(
        "/api/fleet/routes",
        json={"chunk_id": chunk_id, "runner_id": "r1", "workspace_id": "ws1", "environment_ids": ["env1"]},
    )
    assert claim.status_code == 201, claim.text

    hub.client.post(
        "/api/questions",
        json={
            "question_id": "qn_1",
            "chunk_id": chunk_id,
            "runner_id": "r1",
            "epoch": 1,
            "question": "Which way?",
            "asked_at": "2026-07-13T00:00:00+00:00",
        },
        headers=_cookie(admin_token),
    )

    contributor = seed_user(hub, username="cara", role=Role.CONTRIBUTOR)
    token = seed_session(hub, contributor)
    answered = hub.client.post(
        "/api/questions/qn_1/answers",
        json={"answer": "left", "answered_by": "someone-else-entirely"},
        headers=_cookie(token),
    )
    assert answered.status_code == 201, answered.text
    assert answered.json()["answered_by"] == "cara"

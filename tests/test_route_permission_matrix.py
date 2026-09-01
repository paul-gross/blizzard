"""The human-plane route-permission matrix, proven end to end (component tier, #210):
``pending`` is refused everywhere except ``GET /api/me``; ``guest`` reads every
``FLEET_VIEW`` route and is refused every mutation.

Proves the *dynamic* half — an actual session of each role gets the status code the
static route-classification test predicts."""

from __future__ import annotations

from pathlib import Path

import pytest

from blizzard.auth_core import Role
from tests.support import HubHarness, build_hub, seed_session, seed_user

pytestmark = pytest.mark.component

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


def _seed_fixture(hub: HubHarness) -> dict[str, str]:
    """One graph, one ingested (not-yet-promoted) chunk, and one registered runner —
    just enough live data for each ``FLEET_VIEW`` router's detail read to return 200
    rather than 404. The runner is registered straight through the domain service,
    bypassing HTTP entirely, since registration needs no permission of its own to
    seed."""
    admin = seed_user(hub, username="root", role=Role.SUPERUSER)
    admin_token = seed_session(hub, admin)
    graph = hub.client.post("/api/graphs", json={"definition_yaml": _GRAPH_YAML}, headers=_cookie(admin_token))
    assert graph.status_code == 201, graph.text
    chunk = hub.client.post("/api/chunks", json={"tokens": ["default:210"]}, headers=_cookie(admin_token))
    assert chunk.status_code == 201, chunk.text
    hub.services.fleet.register("runner-a", "workspace-1")
    routine = hub.client.post(
        "/api/routines",
        json={
            "name": "matrix-routine",
            "graph_name": "default-delivery",
            "default_scope_slug": "matrix",
            "default_model": [],
            "default_effort": None,
        },
        headers=_cookie(admin_token),
    )
    assert routine.status_code == 201, routine.text
    return {
        "graph_id": graph.json()["graph_id"],
        "chunk_id": chunk.json()["chunk_id"],
        "routine_id": routine.json()["routine_id"],
    }


def _reads(ids: dict[str, str]) -> list[tuple[str, str]]:
    """One representative read per ``FLEET_VIEW``-gated router."""
    return [
        ("GET", "/api/chunks"),
        ("GET", f"/api/chunks/{ids['chunk_id']}"),
        ("GET", f"/api/chunks/{ids['chunk_id']}/work-items"),
        ("GET", "/api/graphs"),
        ("GET", f"/api/graphs/{ids['graph_id']}"),
        ("GET", "/api/queue"),
        ("GET", "/api/runners"),
        ("GET", "/api/runners/runner-a"),
        ("GET", "/api/decisions"),
        ("GET", "/api/questions"),
        ("GET", "/api/spend?since=2026-01-01T00:00:00Z"),
        ("GET", "/api/events"),
        ("GET", "/api/activity"),
        ("GET", "/api/work-sources"),
        ("GET", f"/api/routines/{ids['routine_id']}/baselines"),
    ]


def _mutations(ids: dict[str, str]) -> list[tuple[str, str, dict[str, object]]]:
    """One representative mutation per non-``FLEET_VIEW`` permission — the request
    body is schema-valid throughout, so a denial is proven as the permission gate's
    own 403 rather than an incidental 422/404 that would happen to look the same."""
    return [
        ("POST", "/api/chunks", {"tokens": ["default:211"]}),  # CHUNK_INGEST
        ("POST", f"/api/chunks/{ids['chunk_id']}/promote", {}),  # CHUNK_CONTROL
        ("PUT", "/api/queue", {"chunk_ids": []}),  # QUEUE_REORDER
        (
            "POST",
            "/api/questions",
            {
                "question_id": "qn_matrix",
                "chunk_id": ids["chunk_id"],
                "runner_id": "runner-a",
                "epoch": 1,
                "question": "Which way?",
                "asked_at": "2026-07-13T00:00:00+00:00",
            },
        ),  # QUESTION_ANSWER
        ("POST", "/api/decisions/dc_missing/resolutions", {"choice": "approve", "resolved_by": "x"}),  # GATE_RESOLVE
        ("POST", "/api/runners/runner-a/pause", {"by": "x"}),  # RUNNER_PAUSE
        ("POST", "/api/graphs", {"definition_yaml": _GRAPH_YAML}),  # GRAPH_EDIT
        ("GET", "/api/users", {}),  # USER_MANAGE
        ("POST", "/api/analytics/re-derive", {"limit": 1}),  # ANALYTICS_ADMIN
    ]


def test_guest_reads_every_fleet_view_route_and_is_refused_every_mutation(tmp_path: Path) -> None:
    hub = build_hub(tmp_path, auth_mode="oauth")
    ids = _seed_fixture(hub)
    guest = seed_user(hub, username="reader", role=Role.GUEST)
    token = seed_session(hub, guest)

    for method, path in _reads(ids):
        resp = hub.client.request(method, path, headers=_cookie(token))
        assert resp.status_code == 200, f"{method} {path} -> {resp.status_code}: {resp.text}"

    for method, path, body in _mutations(ids):
        kwargs = {"headers": _cookie(token)} if method == "GET" else {"json": body, "headers": _cookie(token)}
        resp = hub.client.request(method, path, **kwargs)
        assert resp.status_code == 403, f"{method} {path} -> {resp.status_code}: {resp.text}"


def test_pending_is_refused_every_route_but_me(tmp_path: Path) -> None:
    hub = build_hub(tmp_path, auth_mode="oauth")
    ids = _seed_fixture(hub)
    pending = seed_user(hub, username="newcomer", role=Role.PENDING)
    token = seed_session(hub, pending)

    me = hub.client.get("/api/me", headers=_cookie(token))
    assert me.status_code == 200, me.text
    assert me.json()["role"] == "pending"
    assert me.json()["permissions"] == []

    for method, path in _reads(ids):
        resp = hub.client.request(method, path, headers=_cookie(token))
        assert resp.status_code == 403, f"{method} {path} -> {resp.status_code}: {resp.text}"

    for method, path, body in _mutations(ids):
        kwargs = {"headers": _cookie(token)} if method == "GET" else {"json": body, "headers": _cookie(token)}
        resp = hub.client.request(method, path, **kwargs)
        assert resp.status_code == 403, f"{method} {path} -> {resp.status_code}: {resp.text}"

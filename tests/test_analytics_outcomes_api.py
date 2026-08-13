"""The analytics outcomes route (blizzard#256, Phase 4, component tier): the
TRANSCRIPT_READ auth triad, the three cases D4 separates — a judged failure edge, an
attempt that recorded no transition, and a delivery kick-back (bounce) — and D5's two
positional base cases: a chunk whose node position is established by a migration, and a
chunk failing at its entry node with neither a transition nor a migration recorded."""

from __future__ import annotations

from pathlib import Path
from typing import cast

import pytest

from blizzard.auth_core import Role
from blizzard.hub.config import RUNNER_AUTH_ENFORCE
from blizzard.hub.domain.work import IWriteChunkRepository, MigrationSource
from tests.support import HubHarness, build_hub, pointer_token, report_lease, seed_session, seed_user
from tests.test_fleet_auth import _seed_enrolled

pytestmark = pytest.mark.component

_ROUTES = ["/api/analytics/outcomes/nodes"]

#: Named to match the packaged default graph's own name, so `POST /api/chunks` mints
#: onto *this* graph — see `tests/test_analytics_durations_api.py`'s own note.
_GRAPH_YAML = """
name: default-delivery
entry: build
nodes:
  build:
    executor: runner
    prompt: Build it.
    judgement:
      prompt: Assess the build.
      choices:
        pass:
          description: Complete.
          to: review
        fail:
          description: Incomplete.
          to: build
  review:
    executor: runner
    prompt: Review it.
    judgement:
      prompt: Assess the review.
      choices:
        pass:
          description: Complete.
          to: done
"""

_OTHER_GRAPH_YAML = """
name: outcomes-fixture-other
entry: triage
nodes:
  triage:
    executor: runner
    prompt: Triage it.
    judgement:
      prompt: Assess.
      choices:
        pass:
          description: Complete.
          to: done
"""


def _cookie(token: str) -> dict[str, str]:
    return {"Cookie": f"bz_session={token}"}


def _bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _writable(hub: HubHarness) -> IWriteChunkRepository:
    """A test-only cast: ``HubHarness.services.chunks`` is read-typed
    (``bzh:controller-read-only``), but the live object is always the write-capable
    ``ChunkStore`` — mirrors ``tests/test_delivery_conflict_routing.py``'s own helper."""
    return cast(IWriteChunkRepository, hub.services.chunks)


def _seeded_hub(tmp_path: Path):  # type: ignore[no-untyped-def]
    hub = build_hub(tmp_path, auth_mode="oauth")
    admin = seed_user(hub, username="admin", role=Role.ADMIN)
    admin_token = seed_session(hub, admin)
    graph = hub.client.post("/api/graphs", json={"definition_yaml": _GRAPH_YAML}, headers=_cookie(admin_token))
    assert graph.status_code == 201, graph.text
    graph_id = graph.json()["graph_id"]
    nodes = {n["name"]: n["node_id"] for n in graph.json()["nodes"]}

    other = hub.client.post("/api/graphs", json={"definition_yaml": _OTHER_GRAPH_YAML}, headers=_cookie(admin_token))
    assert other.status_code == 201, other.text
    other_graph_id = other.json()["graph_id"]
    other_nodes = {n["name"]: n["node_id"] for n in other.json()["nodes"]}

    contributor = seed_user(hub, username="ada", role=Role.CONTRIBUTOR)
    token = seed_session(hub, contributor)
    return hub, token, graph_id, nodes, other_graph_id, other_nodes


def _mint_chunk(hub, token: str, *, ref: str = "1") -> str:  # type: ignore[no-untyped-def]
    resp = hub.client.post(
        "/api/chunks", json={"tokens": [pointer_token({"source": "default", "ref": ref})]}, headers=_cookie(token)
    )
    assert resp.status_code == 201, resp.text
    return str(resp.json()["chunk_id"])


def _complete(hub, chunk_id: str, *, epoch: int, from_node_id: str, choice: str) -> None:  # type: ignore[no-untyped-def]
    resp = hub.client.post(
        f"/api/fleet/chunks/{chunk_id}/completions",
        json={"choice": choice, "epoch": epoch, "runner_id": "r1", "from_node_id": from_node_id},
    )
    assert resp.status_code == 200, resp.text


# --- auth triad: 401 / 403 / 200, plus the runner-principal refusal ---------------


@pytest.mark.parametrize("path", _ROUTES)
def test_every_route_is_401_with_no_session(tmp_path: Path, path: str) -> None:
    hub = build_hub(tmp_path, auth_mode="oauth")
    resp = hub.client.get(path)
    assert resp.status_code == 401


@pytest.mark.parametrize("path", _ROUTES)
def test_every_route_is_403_below_transcript_read(tmp_path: Path, path: str) -> None:
    hub = build_hub(tmp_path, auth_mode="oauth")
    guest = seed_user(hub, username="grace", role=Role.GUEST)
    token = seed_session(hub, guest)

    resp = hub.client.get(path, headers=_cookie(token))
    assert resp.status_code == 403


@pytest.mark.parametrize("path", _ROUTES)
def test_every_route_refuses_a_runner_principal(tmp_path: Path, path: str) -> None:
    token = _seed_enrolled(tmp_path, runner_id="runner-a")
    hub = build_hub(tmp_path, auth_mode="oauth", runner_auth_mode=RUNNER_AUTH_ENFORCE)

    resp = hub.client.get(path, headers=_bearer(token))
    assert resp.status_code == 403


# --- D4: the three cases, kept separate -----------------------------------------------


def test_a_judged_failure_edge_counts_as_a_choice_not_an_attempt_failure(tmp_path: Path) -> None:
    hub, token, _graph_id, nodes, _og, _on = _seeded_hub(tmp_path)
    chunk_id = _mint_chunk(hub, token)
    report_lease(hub, chunk_id, epoch=1, seq=1)
    _complete(hub, chunk_id, epoch=1, from_node_id=nodes["build"], choice="fail")

    resp = hub.client.get("/api/analytics/outcomes/nodes", headers=_cookie(token))

    assert resp.status_code == 200, resp.text
    assert resp.json()["outcomes"] == [{"node_id": nodes["build"], "choice_counts": {"fail": 1}, "attempt_failures": 0}]


def test_an_attempt_with_no_transition_counts_as_an_attempt_failure(tmp_path: Path) -> None:
    """Also D5's second base case: the chunk's very first epoch, so there is no prior
    movement at all — the node resolves to the pinned graph's own entry node."""
    hub, token, _graph_id, nodes, _og, _on = _seeded_hub(tmp_path)
    chunk_id = _mint_chunk(hub, token)
    report_lease(hub, chunk_id, epoch=1, seq=1)  # a crash/reap — never completed

    resp = hub.client.get("/api/analytics/outcomes/nodes", headers=_cookie(token))

    assert resp.json()["outcomes"] == [{"node_id": nodes["build"], "choice_counts": {}, "attempt_failures": 1}]


def test_a_bounce_counts_as_neither(tmp_path: Path) -> None:
    hub, token, _graph_id, _nodes, _og, _on = _seeded_hub(tmp_path)
    chunk_id = _mint_chunk(hub, token)
    report_lease(hub, chunk_id, epoch=1, seq=1)
    _writable(hub).record_bounce(chunk_id, epoch=1, cause="conflict", envelope="{}", at=hub.clock.now())

    resp = hub.client.get("/api/analytics/outcomes/nodes", headers=_cookie(token))

    assert resp.status_code == 200, resp.text
    assert resp.json()["outcomes"] == []


# --- D5: the migration-derived base case -----------------------------------------------


def test_a_failed_attempt_after_a_migration_resolves_via_the_migrations_landed_node(tmp_path: Path) -> None:
    hub, token, graph_id, nodes, other_graph_id, other_nodes = _seeded_hub(tmp_path)
    chunk_id = _mint_chunk(hub, token)
    report_lease(hub, chunk_id, epoch=1, seq=1)
    _writable(hub).record_migration(
        chunk_id,
        from_node_id=nodes["build"],
        from_graph_id=graph_id,
        to_graph_id=other_graph_id,
        landed_node_id=other_nodes["triage"],
        choice_name="pass",
        model=None,
        epoch=1,
        at=hub.clock.now(),
        artifacts=[],
        source=MigrationSource.AUTHORED_EDGE,
    )
    report_lease(hub, chunk_id, epoch=2, seq=2)  # a crash/reap at the landed node — never completed

    resp = hub.client.get("/api/analytics/outcomes/nodes", headers=_cookie(token))

    assert resp.status_code == 200, resp.text
    outcomes = {o["node_id"]: o for o in resp.json()["outcomes"]}
    assert outcomes[other_nodes["triage"]]["attempt_failures"] == 1
    assert nodes["build"] not in outcomes

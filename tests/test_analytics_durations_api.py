"""The analytics durations routes (blizzard#256, Phase 2, component tier): the
TRANSCRIPT_READ auth triad, real completed-step rollups by node and by graph, every
shared filter (D7), the epoch-join's resistance to a duplicate lease row (A7), a hub
step's own exit never faking a zero, two transitions sharing one epoch chaining their
intervals rather than double-counting, and a `since` edge inside a chained epoch."""

from __future__ import annotations

from datetime import timedelta
from pathlib import Path

import pytest

from blizzard.auth_core import Role
from blizzard.hub.config import RUNNER_AUTH_ENFORCE
from tests.support import (
    FakeHubCommandRunner,
    FakeHubWorkdir,
    FakeWorkSource,
    build_hub,
    pointer_token,
    report_lease,
    seed_session,
    seed_user,
)
from tests.test_fleet_auth import _seed_enrolled

pytestmark = pytest.mark.component

#: Every route this module holds to the auth triad — one list, three sweeps over it.
_ROUTES = ["/api/analytics/durations/nodes", "/api/analytics/durations/graphs"]

#: Named to match the packaged default graph's own name, so `POST /api/chunks` mints
#: onto *this* graph — `GraphMintService.ensure_default` is idempotent by name.
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

#: A runner step feeding a hub-executed one (review round 1 F2) — the hub node's own
#: exit transition is written synchronously by the same completion that enters it.
_HUB_STEP_GRAPH_YAML = """
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
          to: merge
        fail:
          description: Incomplete.
          to: build
  merge:
    executor: hub
    run:
      - name: land
        command: land-the-repo
        produces: merged
    judgement:
      choices:
        success:
          description: Landed.
          to: done
        failure:
          description: Failed to land.
          to: build
"""

#: A human gate reached by a runner step — entering it and resolving it later share one
#: epoch, since resolving a gate mints no new lease (review round 1 F3/F4).
_GATE_GRAPH_YAML = """
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
          to: approve
        fail:
          description: Incomplete.
          to: build
  approve:
    executor: runner
    judgement:
      by: human
      choices:
        pass:
          description: Approved.
          to: done
"""


def _cookie(token: str) -> dict[str, str]:
    return {"Cookie": f"bz_session={token}"}


def _bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _seeded_hub(  # type: ignore[no-untyped-def]
    tmp_path: Path,
    *,
    work_sources: dict[str, FakeWorkSource] | None = None,
    graph_yaml: str = _GRAPH_YAML,
    hub_command_runner: FakeHubCommandRunner | None = None,
    hub_workdir: FakeHubWorkdir | None = None,
):
    hub = build_hub(
        tmp_path,
        auth_mode="oauth",
        work_sources=work_sources,
        hub_command_runner=hub_command_runner,
        hub_workdir=hub_workdir,
    )
    admin = seed_user(hub, username="admin", role=Role.ADMIN)
    admin_token = seed_session(hub, admin)
    graph = hub.client.post("/api/graphs", json={"definition_yaml": graph_yaml}, headers=_cookie(admin_token))
    assert graph.status_code == 201, graph.text
    graph_id = graph.json()["graph_id"]
    nodes = {n["name"]: n["node_id"] for n in graph.json()["nodes"]}

    contributor = seed_user(hub, username="ada", role=Role.CONTRIBUTOR)
    token = seed_session(hub, contributor)
    return hub, token, graph_id, nodes


def _mint_chunk(hub, token: str, *, source: str = "default", ref: str = "1") -> str:  # type: ignore[no-untyped-def]
    resp = hub.client.post(
        "/api/chunks", json={"tokens": [pointer_token({"source": source, "ref": ref})]}, headers=_cookie(token)
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


# --- real rollups, grouped by node and by graph -------------------------------------


def test_durations_by_node_rolls_up_a_completed_step(tmp_path: Path) -> None:
    hub, token, _graph_id, nodes = _seeded_hub(tmp_path)
    chunk_id = _mint_chunk(hub, token)
    report_lease(hub, chunk_id, epoch=1, seq=1)
    hub.clock.advance(timedelta(seconds=30))
    _complete(hub, chunk_id, epoch=1, from_node_id=nodes["build"], choice="pass")

    resp = hub.client.get("/api/analytics/durations/nodes", headers=_cookie(token))

    assert resp.status_code == 200, resp.text
    assert resp.json()["durations"] == [
        {"key": nodes["build"], "completed_steps": 1, "total_seconds": 30.0, "avg_seconds": 30.0}
    ]


def test_durations_by_node_averages_across_two_completed_steps(tmp_path: Path) -> None:
    hub, token, _graph_id, nodes = _seeded_hub(tmp_path)
    chunk_id = _mint_chunk(hub, token)
    report_lease(hub, chunk_id, epoch=1, seq=1)
    hub.clock.advance(timedelta(seconds=10))
    _complete(hub, chunk_id, epoch=1, from_node_id=nodes["build"], choice="fail")
    report_lease(hub, chunk_id, epoch=2, seq=2)
    hub.clock.advance(timedelta(seconds=20))
    _complete(hub, chunk_id, epoch=2, from_node_id=nodes["build"], choice="pass")

    resp = hub.client.get("/api/analytics/durations/nodes", headers=_cookie(token))

    assert resp.json()["durations"] == [
        {"key": nodes["build"], "completed_steps": 2, "total_seconds": 30.0, "avg_seconds": 15.0}
    ]


def test_durations_by_graph_groups_on_the_transitions_own_graph_id(tmp_path: Path) -> None:
    hub, token, graph_id, nodes = _seeded_hub(tmp_path)
    chunk_id = _mint_chunk(hub, token)
    report_lease(hub, chunk_id, epoch=1, seq=1)
    hub.clock.advance(timedelta(seconds=5))
    _complete(hub, chunk_id, epoch=1, from_node_id=nodes["build"], choice="pass")

    resp = hub.client.get("/api/analytics/durations/graphs", headers=_cookie(token))

    assert resp.json()["durations"] == [
        {"key": graph_id, "completed_steps": 1, "total_seconds": 5.0, "avg_seconds": 5.0}
    ]


def test_an_attempt_with_no_transition_contributes_no_duration(tmp_path: Path) -> None:
    hub, token, _graph_id, _nodes = _seeded_hub(tmp_path)
    chunk_id = _mint_chunk(hub, token)
    report_lease(hub, chunk_id, epoch=1, seq=1)  # never completed — a crash/reap, not a step

    resp = hub.client.get("/api/analytics/durations/nodes", headers=_cookie(token))

    assert resp.status_code == 200, resp.text
    assert resp.json()["durations"] == []


def test_a_duplicate_lease_row_does_not_fan_out_the_join(tmp_path: Path) -> None:
    """A7: ``record_lease`` is a bare insert with no unique constraint, so a retried
    ``lease.minted`` report must not double the step this join sees, nor drift its
    duration off the earliest mint."""
    hub, token, _graph_id, nodes = _seeded_hub(tmp_path)
    chunk_id = _mint_chunk(hub, token)
    report_lease(hub, chunk_id, epoch=1, seq=1)
    hub.clock.advance(timedelta(seconds=5))
    report_lease(hub, chunk_id, epoch=1, seq=2)  # duplicate mint at the same epoch, later
    hub.clock.advance(timedelta(seconds=25))
    _complete(hub, chunk_id, epoch=1, from_node_id=nodes["build"], choice="pass")

    resp = hub.client.get("/api/analytics/durations/nodes", headers=_cookie(token))

    assert resp.json()["durations"] == [
        {"key": nodes["build"], "completed_steps": 1, "total_seconds": 30.0, "avg_seconds": 30.0}
    ]


def test_a_hub_executed_steps_own_exit_transition_carries_no_duration(tmp_path: Path) -> None:
    """A hub-executed node's own exit shares one instant with its own synthetic lease
    mint by construction (review round 1 F2) — the real predecessor step shows up, the
    hub's own instantaneous one does not."""
    runner = FakeHubCommandRunner()
    workdir = FakeHubWorkdir()
    hub, token, _graph_id, nodes = _seeded_hub(
        tmp_path, graph_yaml=_HUB_STEP_GRAPH_YAML, hub_command_runner=runner, hub_workdir=workdir
    )
    chunk_id = _mint_chunk(hub, token)
    report_lease(hub, chunk_id, epoch=1, seq=1)
    hub.clock.advance(timedelta(seconds=30))
    complete = hub.client.post(
        f"/api/fleet/chunks/{chunk_id}/completions",
        json={
            "choice": "pass",
            "epoch": 1,
            "runner_id": "r1",
            "from_node_id": nodes["build"],
            "check_results": [],
            "artifacts": [],
        },
    )
    assert complete.status_code == 200, complete.text
    assert complete.json()["outcome"] == "hub_node_taken", complete.text
    assert hub.client.get(f"/api/chunks/{chunk_id}", headers=_cookie(token)).json()["status"] == "done"

    resp = hub.client.get("/api/analytics/durations/nodes", headers=_cookie(token))

    assert resp.json()["durations"] == [
        {"key": nodes["build"], "completed_steps": 1, "total_seconds": 30.0, "avg_seconds": 30.0}
    ]


def test_two_transitions_sharing_one_epoch_chain_their_intervals(tmp_path: Path) -> None:
    """A gate's entry and its later resolution both carry the epoch that led the chunk
    there — resolving a gate mints no new lease. Each interval measures from the movement
    before it, or the gate's wait folds into the step leading to it (review round 1 F3/F4)."""
    hub, token, _graph_id, nodes = _seeded_hub(tmp_path, graph_yaml=_GATE_GRAPH_YAML)
    chunk_id = _mint_chunk(hub, token)
    report_lease(hub, chunk_id, epoch=1, seq=1)
    hub.clock.advance(timedelta(seconds=10))  # the runner's real build time
    entering = hub.client.post(
        f"/api/fleet/chunks/{chunk_id}/completions",
        json={"choice": "pass", "epoch": 1, "runner_id": "r1", "from_node_id": nodes["build"], "artifacts": []},
    )
    assert entering.status_code == 200, entering.text
    decision_id = hub.client.get(f"/api/chunks/{chunk_id}", headers=_cookie(token)).json()["decision"]["decision_id"]

    hub.clock.advance(timedelta(seconds=100))  # the human's wait — must not land on `build`
    resolve = hub.client.post(
        f"/api/decisions/{decision_id}/resolutions", json={"choice": "pass"}, headers=_cookie(token)
    )
    assert resolve.status_code == 200, resolve.text
    resolving = hub.client.post(
        f"/api/fleet/chunks/{chunk_id}/completions",
        json={
            "choice": "pass",
            "epoch": 1,
            "runner_id": "r1",
            "from_node_id": nodes["approve"],
            "decision_id": decision_id,
            "artifacts": [],
        },
    )
    assert resolving.status_code == 200, resolving.text
    assert hub.client.get(f"/api/chunks/{chunk_id}", headers=_cookie(token)).json()["status"] == "done"

    resp = hub.client.get("/api/analytics/durations/nodes", headers=_cookie(token))

    by_node = {row["key"]: row for row in resp.json()["durations"]}
    assert by_node[nodes["build"]] == {
        "key": nodes["build"],
        "completed_steps": 1,
        "total_seconds": 10.0,
        "avg_seconds": 10.0,
    }
    assert by_node[nodes["approve"]] == {
        "key": nodes["approve"],
        "completed_steps": 1,
        "total_seconds": 100.0,
        "avg_seconds": 100.0,
    }


# --- the shared filter vocabulary (D7) -----------------------------------------------


def test_the_time_range_filter_does_not_break_a_chained_epoch(tmp_path: Path) -> None:
    """A window edge between two transitions sharing one epoch must not re-anchor the
    survivor to the epoch's mint — its interval still measures from its true
    predecessor, and the excluded transition's own row simply does not appear."""
    hub, token, _graph_id, nodes = _seeded_hub(tmp_path, graph_yaml=_GATE_GRAPH_YAML)
    chunk_id = _mint_chunk(hub, token)
    report_lease(hub, chunk_id, epoch=1, seq=1)
    hub.clock.advance(timedelta(seconds=10))
    entering = hub.client.post(
        f"/api/fleet/chunks/{chunk_id}/completions",
        json={"choice": "pass", "epoch": 1, "runner_id": "r1", "from_node_id": nodes["build"], "artifacts": []},
    )
    assert entering.status_code == 200, entering.text
    decision_id = hub.client.get(f"/api/chunks/{chunk_id}", headers=_cookie(token)).json()["decision"]["decision_id"]

    hub.clock.advance(timedelta(milliseconds=1))
    since = hub.clock.now().isoformat()  # strictly after `build`'s own transition
    hub.clock.advance(timedelta(seconds=99, milliseconds=999))  # 100s after it, total

    resolve = hub.client.post(
        f"/api/decisions/{decision_id}/resolutions", json={"choice": "pass"}, headers=_cookie(token)
    )
    assert resolve.status_code == 200, resolve.text
    resolving = hub.client.post(
        f"/api/fleet/chunks/{chunk_id}/completions",
        json={
            "choice": "pass",
            "epoch": 1,
            "runner_id": "r1",
            "from_node_id": nodes["approve"],
            "decision_id": decision_id,
            "artifacts": [],
        },
    )
    assert resolving.status_code == 200, resolving.text

    resp = hub.client.get("/api/analytics/durations/nodes", params={"since": since}, headers=_cookie(token))

    by_node = {row["key"]: row for row in resp.json()["durations"]}
    assert nodes["build"] not in by_node  # its own exit transition predates the window
    assert by_node[nodes["approve"]] == {
        "key": nodes["approve"],
        "completed_steps": 1,
        "total_seconds": 100.0,
        "avg_seconds": 100.0,
    }


def test_durations_honor_the_graph_id_filter(tmp_path: Path) -> None:
    hub, token, graph_id, nodes = _seeded_hub(tmp_path)
    chunk_id = _mint_chunk(hub, token)
    report_lease(hub, chunk_id, epoch=1, seq=1)
    hub.clock.advance(timedelta(seconds=1))
    _complete(hub, chunk_id, epoch=1, from_node_id=nodes["build"], choice="pass")

    resp = hub.client.get("/api/analytics/durations/nodes", params={"graph_id": graph_id}, headers=_cookie(token))
    assert resp.json()["durations"] == [
        {"key": nodes["build"], "completed_steps": 1, "total_seconds": 1.0, "avg_seconds": 1.0}
    ]

    resp_other = hub.client.get(
        "/api/analytics/durations/nodes", params={"graph_id": "gr_nonexistent"}, headers=_cookie(token)
    )
    assert resp_other.json()["durations"] == []


def test_durations_honor_the_source_filter(tmp_path: Path) -> None:
    hub, token, _graph_id, nodes = _seeded_hub(
        tmp_path, work_sources={"default": FakeWorkSource(), "other": FakeWorkSource(name="other")}
    )
    chunk_a = _mint_chunk(hub, token, source="default", ref="1")
    chunk_b = _mint_chunk(hub, token, source="other", ref="2")
    for seq, chunk_id in enumerate((chunk_a, chunk_b), start=1):
        report_lease(hub, chunk_id, epoch=1, seq=seq)
        hub.clock.advance(timedelta(seconds=1))
        _complete(hub, chunk_id, epoch=1, from_node_id=nodes["build"], choice="pass")

    resp = hub.client.get("/api/analytics/durations/nodes", params={"source": "other"}, headers=_cookie(token))

    assert resp.json()["durations"] == [
        {"key": nodes["build"], "completed_steps": 1, "total_seconds": 1.0, "avg_seconds": 1.0}
    ]


def test_durations_honor_the_time_range_filter(tmp_path: Path) -> None:
    hub, token, _graph_id, nodes = _seeded_hub(tmp_path)
    chunk_id = _mint_chunk(hub, token)
    report_lease(hub, chunk_id, epoch=1, seq=1)
    hub.clock.advance(timedelta(seconds=1))
    _complete(hub, chunk_id, epoch=1, from_node_id=nodes["build"], choice="fail")
    hub.clock.advance(timedelta(milliseconds=1))
    before_second = hub.clock.now().isoformat()
    report_lease(hub, chunk_id, epoch=2, seq=2)
    hub.clock.advance(timedelta(seconds=1))
    _complete(hub, chunk_id, epoch=2, from_node_id=nodes["build"], choice="pass")

    resp = hub.client.get("/api/analytics/durations/nodes", params={"since": before_second}, headers=_cookie(token))

    assert resp.json()["durations"] == [
        {"key": nodes["build"], "completed_steps": 1, "total_seconds": 1.0, "avg_seconds": 1.0}
    ]

"""The analytics outcomes route (blizzard#256, Phase 4, component tier): the
TRANSCRIPT_READ auth triad, the three cases D4 separates — a judged failure edge, an
attempt that recorded no transition, and a delivery kick-back (bounce) — D5's two
positional base cases, a no-movement failure resolving via the epoch's own graph, and
the shared filter vocabulary (D7)."""

from __future__ import annotations

from datetime import timedelta
from pathlib import Path
from typing import cast

import pytest

from blizzard.auth_core import Role
from blizzard.hub.config import RUNNER_AUTH_ENFORCE
from blizzard.hub.delivery.command_runner import CommandResult
from blizzard.hub.domain.chunks.escalations import IWriteChunkEscalationsRepository
from blizzard.hub.domain.chunks.movement import IWriteChunkMovementRepository
from blizzard.hub.domain.work import MigrationSource
from blizzard.hub.graphs.scripts import land_pr_ci
from tests.support import (
    FakeHubCommandRunner,
    FakeHubWorkdir,
    FakeWorkSource,
    HubHarness,
    build_hub,
    pointer_token,
    report_lease,
    seed_session,
    seed_user,
)
from tests.test_delivery_conflict_routing import (
    _LAND_COMMAND,
    _mint_and_claim,
    _seed_at_deliver_with_an_unlanded_commit,
)
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


def _writable_movement(hub: HubHarness) -> IWriteChunkMovementRepository:
    """A test-only cast: ``HubHarness.services.chunks`` is read-typed
    (``bzh:controller-read-only``), but the live object is always the write-capable
    seam adapter — mirrors ``tests/test_delivery_conflict_routing.py``'s own helper."""
    return cast(IWriteChunkMovementRepository, hub.services.chunks.movement)


def _writable_escalations(hub: HubHarness) -> IWriteChunkEscalationsRepository:
    """Same as :func:`_writable_movement`, for the chunk-escalations seam."""
    return cast(IWriteChunkEscalationsRepository, hub.services.chunks.escalations)


def _seeded_hub(tmp_path: Path, *, work_sources: dict[str, FakeWorkSource] | None = None):  # type: ignore[no-untyped-def]
    hub = build_hub(tmp_path, auth_mode="oauth", work_sources=work_sources)
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
    """Also D5's second base case: no prior movement, so the node resolves to the
    pinned graph's entry. Epoch 2's lease is epoch 1's positive end-of-attempt evidence
    (review round 1 F1); epoch 2 itself, still the newest, counts as none."""
    hub, token, _graph_id, nodes, _og, _on = _seeded_hub(tmp_path)
    chunk_id = _mint_chunk(hub, token)
    report_lease(hub, chunk_id, epoch=1, seq=1)  # a crash/reap — never completed
    report_lease(hub, chunk_id, epoch=2, seq=2)  # proves epoch 1 is over; itself in-flight

    resp = hub.client.get("/api/analytics/outcomes/nodes", headers=_cookie(token))

    assert resp.json()["outcomes"] == [{"node_id": nodes["build"], "choice_counts": {}, "attempt_failures": 1}]


def test_a_bounce_counts_as_neither(tmp_path: Path) -> None:
    """F1 (review round 4): the bounced epoch must be superseded by a strictly newer
    lease, or the unrelated in-flight guard excludes it first and this test can never
    detect the bounce-exclusion guard being deleted."""
    hub, token, _graph_id, _nodes, _og, _on = _seeded_hub(tmp_path)
    chunk_id = _mint_chunk(hub, token)
    report_lease(hub, chunk_id, epoch=1, seq=1)
    _writable_escalations(hub).record_bounce(chunk_id, epoch=1, cause="conflict", envelope="{}", at=hub.clock.now())
    report_lease(hub, chunk_id, epoch=2, seq=2)  # supersedes epoch 1, proving it's over

    resp = hub.client.get("/api/analytics/outcomes/nodes", headers=_cookie(token))

    assert resp.status_code == 200, resp.text
    assert resp.json()["outcomes"] == []


def test_a_bounced_transitions_own_routing_edge_counts_as_neither(tmp_path: Path) -> None:
    """A kick-back's own routing transition shares its epoch with the bounce fact by
    construction (`hub_node.py` records both). Driven through the REAL hub-node delivery
    path, not a bare bounce write, so the excluded transition actually exists."""
    runner = FakeHubCommandRunner()
    runner.arm(_LAND_COMMAND, CommandResult(exit_code=0, stdout=f"doing stuff\n{land_pr_ci._CONFLICT}\n", stderr=""))
    hub = build_hub(tmp_path, hub_command_runner=runner, hub_workdir=FakeHubWorkdir())
    chunk_id, nodes = _mint_and_claim(hub)
    _seed_at_deliver_with_an_unlanded_commit(hub, chunk_id, nodes)
    report_lease(hub, chunk_id, epoch=1, seq=1)

    advance = hub.client.post(f"/api/fleet/chunks/{chunk_id}/hub-advance")
    body = advance.json()
    assert body["ran"] is True
    assert body["outcome_choice"] == "conflict"
    detail = hub.client.get(f"/api/chunks/{chunk_id}").json()
    assert len(detail["bounces"]) == 1  # the kick-back landed
    assert detail["current_node_id"] == nodes["resolve"]  # ...and so did its own transition

    resp = hub.client.get("/api/analytics/outcomes/nodes")

    assert resp.status_code == 200, resp.text
    outcomes = {o["node_id"]: o for o in resp.json()["outcomes"]}
    assert nodes["deliver"] not in outcomes


def test_the_bounce_exclusion_reaches_the_kick_backs_own_epoch_and_no_further(tmp_path: Path) -> None:
    """The exclusion's grain is ``(chunk_id, epoch)``, and the next node-step's own
    judged choice on the SAME chunk still counts — the hub step claims a synthetic lease
    at its epoch, so the runner's next lease mints strictly above it and never collides."""
    runner = FakeHubCommandRunner()
    runner.arm(_LAND_COMMAND, CommandResult(exit_code=0, stdout=f"doing stuff\n{land_pr_ci._CONFLICT}\n", stderr=""))
    hub = build_hub(tmp_path, hub_command_runner=runner, hub_workdir=FakeHubWorkdir())
    chunk_id, nodes = _mint_and_claim(hub)
    _seed_at_deliver_with_an_unlanded_commit(hub, chunk_id, nodes)
    report_lease(hub, chunk_id, epoch=1, seq=1)
    assert hub.client.post(f"/api/fleet/chunks/{chunk_id}/hub-advance").json()["outcome_choice"] == "conflict"

    envelope = hub.client.get(f"/api/fleet/chunks/{chunk_id}/envelope").json()
    assert envelope["node"]["node_name"] == "resolve"
    next_epoch = envelope["epoch"] + 1  # what `Spawner._mint` derives, above the hub step's own
    report_lease(hub, chunk_id, epoch=next_epoch, seq=2)
    completed = hub.client.post(
        f"/api/fleet/chunks/{chunk_id}/completions",
        json={
            "choice": "resolved",
            "epoch": next_epoch,
            "runner_id": "r1",
            "from_node_id": nodes["resolve"],
            "artifacts": [],
        },
    )
    assert completed.status_code == 200, completed.text

    resp = hub.client.get("/api/analytics/outcomes/nodes")

    outcomes = {o["node_id"]: o for o in resp.json()["outcomes"]}
    assert nodes["deliver"] not in outcomes  # still excluded
    assert outcomes[nodes["resolve"]]["choice_counts"] == {"resolved": 1}  # ...and no wider


# --- D5: the migration-derived base case -----------------------------------------------


def test_a_failed_attempt_after_a_migration_resolves_via_the_migrations_landed_node(tmp_path: Path) -> None:
    hub, token, graph_id, nodes, other_graph_id, other_nodes = _seeded_hub(tmp_path)
    chunk_id = _mint_chunk(hub, token)
    report_lease(hub, chunk_id, epoch=1, seq=1)
    _writable_movement(hub).record_migration(
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
        proposals=[],
        source=MigrationSource.AUTHORED_EDGE,
    )
    report_lease(hub, chunk_id, epoch=2, seq=2)  # a crash/reap at the landed node — never completed
    report_lease(hub, chunk_id, epoch=3, seq=3)  # proves epoch 2 is over (review round 1 F1)

    resp = hub.client.get("/api/analytics/outcomes/nodes", headers=_cookie(token))

    assert resp.status_code == 200, resp.text
    outcomes = {o["node_id"]: o for o in resp.json()["outcomes"]}
    assert outcomes[other_nodes["triage"]]["attempt_failures"] == 1
    assert nodes["build"] not in outcomes


def test_a_null_landed_node_migration_resolves_via_the_target_graphs_entry_node(tmp_path: Path) -> None:
    """``landed_node_id`` null means "target entry" — must resolve via the migration's
    own ``to_graph_id`` rather than crash on a raw ``None`` (review round 1 F7)."""
    hub, token, graph_id, nodes, other_graph_id, other_nodes = _seeded_hub(tmp_path)
    chunk_id = _mint_chunk(hub, token)
    report_lease(hub, chunk_id, epoch=1, seq=1)
    _writable_movement(hub).record_migration(
        chunk_id,
        from_node_id=nodes["build"],
        from_graph_id=graph_id,
        to_graph_id=other_graph_id,
        landed_node_id=None,
        choice_name="pass",
        model=None,
        epoch=1,
        at=hub.clock.now(),
        artifacts=[],
        proposals=[],
        source=MigrationSource.AUTHORED_EDGE,
    )
    report_lease(hub, chunk_id, epoch=2, seq=2)  # a crash/reap at the landed (entry) node
    report_lease(hub, chunk_id, epoch=3, seq=3)  # proves epoch 2 is over (review round 1 F1)

    resp = hub.client.get("/api/analytics/outcomes/nodes", headers=_cookie(token))

    assert resp.status_code == 200, resp.text
    outcomes = {o["node_id"]: o for o in resp.json()["outcomes"]}
    assert outcomes[other_nodes["triage"]]["attempt_failures"] == 1


def test_a_pre_migration_no_movement_failure_resolves_via_the_graph_it_ran_in(tmp_path: Path) -> None:
    """The store half of the same rule the domain fold owns: a real
    ``chunk_migrations`` row's ``from_graph_id`` must reach the fold, so an epoch that
    crashed before ever moving is attributed to the graph it ran in, not the later pin."""
    hub, token, graph_id, nodes, other_graph_id, other_nodes = _seeded_hub(tmp_path)
    chunk_id = _mint_chunk(hub, token)
    report_lease(hub, chunk_id, epoch=1, seq=1)  # crashed on graph A, no transition of its own
    report_lease(hub, chunk_id, epoch=2, seq=2)
    _writable_movement(hub).record_migration(
        chunk_id,
        from_node_id=nodes["build"],
        from_graph_id=graph_id,
        to_graph_id=other_graph_id,
        landed_node_id=other_nodes["triage"],
        choice_name="pass",
        model=None,
        epoch=2,
        at=hub.clock.now(),
        artifacts=[],
        proposals=[],
        source=MigrationSource.AUTHORED_EDGE,
    )

    resp = hub.client.get("/api/analytics/outcomes/nodes", headers=_cookie(token))

    outcomes = {o["node_id"]: o for o in resp.json()["outcomes"]}
    assert outcomes[nodes["build"]]["attempt_failures"] == 1  # graph A's entry
    assert other_nodes["triage"] not in outcomes  # never graph B's, the current pin


# --- the shared filter vocabulary (D7) -----------------------------------------------


def test_outcomes_honor_the_graph_id_filter_on_a_derived_attempt_failure(tmp_path: Path) -> None:
    """`graph_id` applies to D5's own DERIVED graph too — a no-movement failure has no
    transition of its own to filter by, only the graph D5 resolves it to."""
    hub, token, graph_id, nodes, other_graph_id, _on = _seeded_hub(tmp_path)
    chunk_id = _mint_chunk(hub, token)
    report_lease(hub, chunk_id, epoch=1, seq=1)  # a crash/reap — never completed
    report_lease(hub, chunk_id, epoch=2, seq=2)  # proves epoch 1 is over

    matching = hub.client.get("/api/analytics/outcomes/nodes", params={"graph_id": graph_id}, headers=_cookie(token))
    assert matching.json()["outcomes"] == [{"node_id": nodes["build"], "choice_counts": {}, "attempt_failures": 1}]

    other = hub.client.get("/api/analytics/outcomes/nodes", params={"graph_id": other_graph_id}, headers=_cookie(token))
    assert other.json()["outcomes"] == []


def test_outcomes_honor_the_source_filter_on_both_halves(tmp_path: Path) -> None:
    """F12 (review round 4): `source` applies to both the judged-choice half (a
    completion) and the attempt-failure half (a no-movement crash) — durations/spend
    each carry a dedicated `source` test, outcomes did not."""
    hub, token, _graph_id, nodes, _og, _on = _seeded_hub(
        tmp_path, work_sources={"default": FakeWorkSource(), "other": FakeWorkSource(name="other")}
    )
    judged_chunk = _mint_chunk(hub, token, source="default", ref="1")
    report_lease(hub, judged_chunk, epoch=1, seq=1)
    _complete(hub, judged_chunk, epoch=1, from_node_id=nodes["build"], choice="pass")

    failed_chunk = _mint_chunk(hub, token, source="other", ref="2")
    report_lease(hub, failed_chunk, epoch=1, seq=2)  # a crash/reap — never completed
    report_lease(hub, failed_chunk, epoch=2, seq=3)  # proves epoch 1 is over

    matching = hub.client.get("/api/analytics/outcomes/nodes", params={"source": "other"}, headers=_cookie(token))
    assert matching.json()["outcomes"] == [{"node_id": nodes["build"], "choice_counts": {}, "attempt_failures": 1}]

    other = hub.client.get("/api/analytics/outcomes/nodes", params={"source": "default"}, headers=_cookie(token))
    assert other.json()["outcomes"] == [
        {"node_id": nodes["build"], "choice_counts": {"pass": 1}, "attempt_failures": 0}
    ]


def test_outcomes_honor_the_time_range_filter_at_the_lease_mints_own_boundary(tmp_path: Path) -> None:
    """`since`/`until` window the candidate epoch's own lease MINT, not a transition's
    timestamp — `since` inclusive of the mint instant itself, `until` exclusive."""
    hub, token, _graph_id, nodes, _og, _on = _seeded_hub(tmp_path)
    chunk_id = _mint_chunk(hub, token)
    report_lease(hub, chunk_id, epoch=1, seq=1)  # a crash/reap — never completed
    since = hub.clock.now().isoformat()  # exactly epoch 1's own mint instant
    hub.clock.advance(timedelta(milliseconds=1))
    until = hub.clock.now().isoformat()  # strictly after epoch 1's mint
    report_lease(hub, chunk_id, epoch=2, seq=2)  # proves epoch 1 is over

    inside = hub.client.get(
        "/api/analytics/outcomes/nodes", params={"since": since, "until": until}, headers=_cookie(token)
    )
    assert inside.json()["outcomes"] == [{"node_id": nodes["build"], "choice_counts": {}, "attempt_failures": 1}]

    outside = hub.client.get("/api/analytics/outcomes/nodes", params={"since": until}, headers=_cookie(token))
    assert outside.json()["outcomes"] == []

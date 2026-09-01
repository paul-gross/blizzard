"""The ``/chunks/{id}/restart`` route over the HTTP surface (#370, #371).

Proves the operator's forced move end to end: 202/404/409, the durable fact and its bumped
epoch, the stale-epoch rejection the preempted worker's completion meets, the in-flight parks
the move consumes, the artifacts it leaves alone, the fresh session it re-enters on — and, for
a cross-graph move, the re-pin it rides with and the target graph's own stamps."""

from __future__ import annotations

import json
from datetime import timedelta
from typing import cast

import pytest

from blizzard.foundation.node_steps import Executor, SessionMode
from blizzard.foundation.store.engine import create_engine_from_url
from blizzard.hub.domain.chunks.escalations import IWriteChunkEscalationsRepository
from blizzard.hub.domain.chunks.hub_exec import IWriteChunkHubExecRepository
from blizzard.hub.domain.chunks.record import IWriteChunkRecordRepository
from blizzard.hub.domain.restart import SUPERSEDED_ANSWER
from blizzard.hub.domain.work import Movement, MovementKind
from blizzard.tools.invariants import HubInvariants
from tests.support import assert_all_timestamps_utc, build_hub, emitted_events, ingest, report_lease

pytestmark = pytest.mark.component

_POINTER = {"source": "default", "ref": "12"}

_YAML = """
name: default-delivery
entry: plan
sessions:
  main:
    model: [blizzard:advanced]
    effort: high
    compaction_window: "120000"
nodes:
  plan:
    executor: runner
    session: fresh:main
    prompt: Plan it.
    judgement:
      prompt: Assess the plan.
      choices:
        pass:
          description: Planned.
          to: build
  build:
    executor: runner
    session: resume:main
    prompt: Build it.
    judgement:
      prompt: Assess the build.
      choices:
        pass:
          description: Built.
          to: done
        fail:
          description: Incomplete.
          to: build
"""

_GATE_YAML = """
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
          description: Built.
          to: signoff
  signoff:
    executor: runner
    judgement:
      by: human
      choices:
        approve:
          description: Approved.
          to: done
        reject:
          description: Rejected.
          to: build
"""


_TARGET_YAML = """
name: hardened-delivery
entry: build
sessions:
  main:
    model: [blizzard:basic]
    effort: low
    compaction_window: "40000"
nodes:
  build:
    executor: runner
    session: resume:main
    prompt: Build it, carefully.
    judgement:
      prompt: Assess the build.
      choices:
        pass:
          description: Built.
          to: done
  audit:
    executor: runner
    prompt: Audit it.
    judgement:
      prompt: Assess the audit.
      choices:
        pass:
          description: Audited.
          to: done
"""


def _claim(hub, chunk_id: str, *, epoch: int = 1) -> None:  # type: ignore[no-untyped-def]
    resp = hub.client.post(
        "/api/fleet/routes",
        json={"chunk_id": chunk_id, "runner_id": "r1", "workspace_id": "w1", "environment_ids": ["env-a"]},
    )
    assert resp.status_code == 201, resp.text
    report_lease(hub, chunk_id, epoch=epoch, seq=epoch)


def _mint(hub, yaml: str = _YAML) -> str:  # type: ignore[no-untyped-def]
    """A running chunk on ``yaml``'s graph, leased at epoch 1 — the pre-restart state."""
    assert hub.client.post("/api/graphs", json={"definition_yaml": yaml}).status_code == 201
    chunk_id = ingest(hub, [_POINTER])
    _claim(hub, chunk_id)
    return chunk_id


def _detail(hub, chunk_id: str) -> dict:  # type: ignore[no-untyped-def]
    resp = hub.client.get(f"/api/chunks/{chunk_id}")
    assert resp.status_code == 200, resp.text
    return resp.json()


def _restart(hub, chunk_id: str, **body: object):  # type: ignore[no-untyped-def]
    return hub.client.post(f"/api/chunks/{chunk_id}/restart", json=body)


def test_restart_records_the_move_and_bumps_the_epoch(tmp_path) -> None:  # type: ignore[no-untyped-def]
    """The default target is the chunk's current node — restart this step on clean context."""
    hub = build_hub(tmp_path)
    chunk_id = _mint(hub)
    before = _detail(hub, chunk_id)

    resp = _restart(hub, chunk_id)

    assert resp.status_code == 202, resp.text
    detail = _detail(hub, chunk_id)
    assert detail["current_node_id"] == before["current_node_id"]
    assert detail["latest_epoch"] == 2
    assert detail["status"] == "running"
    assert_all_timestamps_utc(detail)


def test_restart_onto_another_node_lands_the_chunk_there(tmp_path) -> None:  # type: ignore[no-untyped-def]
    hub = build_hub(tmp_path)
    chunk_id = _mint(hub)

    assert _restart(hub, chunk_id, node="build").status_code == 202

    assert _detail(hub, chunk_id)["current_node_name"] == "build"


def test_the_move_is_a_durable_fact_distinguishable_from_a_transition(tmp_path) -> None:  # type: ignore[no-untyped-def]
    """Chunk history carries it as its own entry, naming who moved it and to where."""
    hub = build_hub(tmp_path)
    chunk_id = _mint(hub)

    _restart(hub, chunk_id, node="build", by="ada")

    detail = _detail(hub, chunk_id)
    assert detail["history"] == []  # no transition was invented for the move
    assert len(detail["restarts"]) == 1
    move = detail["restarts"][0]
    assert move["to_node_name"] == "build"
    assert move["restarted_by"] == "ada"
    assert move["epoch"] == 2


def test_restart_rejects_a_node_absent_from_the_chunks_graph(tmp_path) -> None:  # type: ignore[no-untyped-def]
    hub = build_hub(tmp_path)
    chunk_id = _mint(hub)

    resp = _restart(hub, chunk_id, node="deploy")

    assert resp.status_code == 409, resp.text
    assert "deploy" in resp.json()["detail"]
    assert _detail(hub, chunk_id)["latest_epoch"] == 1  # refused before anything was written


def test_restart_is_404_for_an_unknown_chunk(tmp_path) -> None:  # type: ignore[no-untyped-def]
    hub = build_hub(tmp_path)
    assert _restart(hub, "ch_nope").status_code == 404


def test_restart_refuses_a_terminal_chunk(tmp_path) -> None:  # type: ignore[no-untyped-def]
    """There is no node to re-enter, and a stopped chunk is never re-derived leasable."""
    hub = build_hub(tmp_path)
    chunk_id = _mint(hub)
    assert hub.client.post(f"/api/chunks/{chunk_id}/stop", json={"by": "operator"}).status_code == 202

    resp = _restart(hub, chunk_id)

    assert resp.status_code == 409, resp.text
    assert _detail(hub, chunk_id)["status"] == "stopped"


def test_a_preempted_workers_completion_is_rejected_by_the_stale_epoch_fence(tmp_path) -> None:  # type: ignore[no-untyped-def]
    """The move's whole point: the displaced worker can lose work but never land it."""
    hub = build_hub(tmp_path)
    chunk_id = _mint(hub)
    node_id = _detail(hub, chunk_id)["current_node_id"]

    assert _restart(hub, chunk_id).status_code == 202

    resp = hub.client.post(
        f"/api/fleet/chunks/{chunk_id}/completions",
        json={"choice": "pass", "epoch": 1, "runner_id": "r1", "from_node_id": node_id, "artifacts": []},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["outcome"] == "failure"
    assert body["detail"] == "stale epoch 1; chunk is at 2"
    assert _detail(hub, chunk_id)["current_node_name"] == "plan"  # the chunk never advanced


def test_restart_answers_an_open_ask_with_the_fixed_system_answer(tmp_path) -> None:  # type: ignore[no-untyped-def]
    """Exactly one answer ever exists, so the move consumes the question normally."""
    hub = build_hub(tmp_path)
    chunk_id = _mint(hub)
    asked = hub.client.post(
        "/api/questions",
        json={
            "question_id": "qn_1",
            "chunk_id": chunk_id,
            "runner_id": "r1",
            "epoch": 1,
            "question": "which branch?",
            "options": [],
            "asked_at": "2026-07-13T12:00:00+00:00",
        },
    )
    assert asked.status_code == 201, asked.text
    assert _detail(hub, chunk_id)["status"] == "waiting_on_human"

    assert _restart(hub, chunk_id).status_code == 202

    detail = _detail(hub, chunk_id)
    assert detail["status"] == "running"
    question = detail["questions"][0]
    assert question["answered"] is True
    assert question["answer"] == SUPERSEDED_ANSWER


def test_a_persons_own_answer_outranks_the_moves(tmp_path) -> None:  # type: ignore[no-untyped-def]
    """First-write-wins holds: the move never overwrites an answer a human already gave."""
    hub = build_hub(tmp_path)
    chunk_id = _mint(hub)
    hub.client.post(
        "/api/questions",
        json={
            "question_id": "qn_1",
            "chunk_id": chunk_id,
            "runner_id": "r1",
            "epoch": 1,
            "question": "which branch?",
            "options": [],
            "asked_at": "2026-07-13T12:00:00+00:00",
        },
    )
    assert hub.client.post("/api/questions/qn_1/answers", json={"answer": "main"}).status_code == 201

    assert _restart(hub, chunk_id).status_code == 202

    assert _detail(hub, chunk_id)["questions"][0]["answer"] == "main"


def test_restart_closes_an_open_gate_decision(tmp_path) -> None:  # type: ignore[no-untyped-def]
    """A resolving fact is written, so the chunk stops deriving ``waiting_on_human`` — and
    no choice is invented, so nothing downstream transitions along one."""
    hub = build_hub(tmp_path)
    chunk_id = _mint(hub, _GATE_YAML)
    build_node = _detail(hub, chunk_id)["current_node_id"]
    parked = hub.client.post(
        f"/api/fleet/chunks/{chunk_id}/completions",
        json={"choice": "pass", "epoch": 1, "runner_id": "r1", "from_node_id": build_node, "artifacts": []},
    )
    assert parked.json()["outcome"] == "parked_at_gate", parked.text
    assert _detail(hub, chunk_id)["status"] == "waiting_on_human"

    assert _restart(hub, chunk_id, node="build").status_code == 202

    detail = _detail(hub, chunk_id)
    assert detail["status"] == "running"
    assert detail["decision"] is None  # nothing is left for the runner to act on
    assert detail["restarts"][0]["decision_id"] is not None


def test_restart_supersedes_an_open_escalation(tmp_path) -> None:  # type: ignore[no-untyped-def]
    """Escalations carry no resolution fact, so the move closes this one by supersession."""
    hub = build_hub(tmp_path)
    chunk_id = _mint(hub)
    cast(IWriteChunkEscalationsRepository, hub.services.chunks.escalations).record_escalation(
        chunk_id, epoch=1, takeover_command="resume it", at=hub.clock.now(), wrapped_takeover_command=""
    )
    assert _detail(hub, chunk_id)["status"] == "needs_human"

    hub.clock.advance(timedelta(seconds=1))
    assert _restart(hub, chunk_id).status_code == 202

    detail = _detail(hub, chunk_id)
    assert detail["status"] == "running"
    assert detail["escalation"] is None


def test_artifacts_from_the_superseded_step_stay_readable(tmp_path) -> None:  # type: ignore[no-untyped-def]
    """The move leaves the work already produced in place — only the session is discarded."""
    hub = build_hub(tmp_path)
    chunk_id = _mint(hub)
    plan_node = _detail(hub, chunk_id)["current_node_id"]
    advanced = hub.client.post(
        f"/api/fleet/chunks/{chunk_id}/completions",
        json={
            "choice": "pass",
            "epoch": 1,
            "runner_id": "r1",
            "from_node_id": plan_node,
            "artifacts": [{"kind": "asset", "name": "notes", "content": "half a plan"}],
        },
    )
    assert advanced.json()["outcome"] == "next", advanced.text

    assert _restart(hub, chunk_id, node="plan").status_code == 202

    artifacts = _detail(hub, chunk_id)["artifacts"]
    assert [(a["name"], a["content"]) for a in artifacts] == [("notes", "half a plan")]


def test_restart_works_on_a_chunk_with_no_live_lease(tmp_path) -> None:  # type: ignore[no-untyped-def]
    """Valid whether or not the chunk is leased: an unclaimed ready chunk moves too, and
    the next claim's envelope is what carries the move to whoever takes it."""
    hub = build_hub(tmp_path)
    assert hub.client.post("/api/graphs", json={"definition_yaml": _YAML}).status_code == 201
    chunk_id = ingest(hub, [_POINTER])
    assert _detail(hub, chunk_id)["status"] == "ready"

    assert _restart(hub, chunk_id, node="build").status_code == 202

    detail = _detail(hub, chunk_id)
    assert detail["status"] == "ready"
    assert detail["current_node_name"] == "build"
    assert detail["latest_epoch"] == 1


def test_the_envelope_after_a_restart_declares_a_fresh_session(tmp_path) -> None:  # type: ignore[no-untyped-def]
    """The node declares ``resume:main``; the forced visit overrides it, so the re-entry
    mints rather than continuing the pool head, and stamps the pool's declared config."""
    hub = build_hub(tmp_path)
    chunk_id = _mint(hub)

    assert _restart(hub, chunk_id, node="build").status_code == 202

    node = hub.client.get(f"/api/fleet/chunks/{chunk_id}/envelope").json()["node"]
    assert node["node_name"] == "build"
    assert node["session"] == SessionMode.FRESH.value
    assert node["session_name"] == "main"
    assert node["session_model"] == ["blizzard:advanced"]
    assert node["session_effort"] == "high"
    assert node["session_compaction_window"] == "120000"


def test_a_transition_off_the_forced_visit_restores_the_declared_session_mode(tmp_path) -> None:  # type: ignore[no-untyped-def]
    """The override lasts exactly as long as the visit the move forced."""
    hub = build_hub(tmp_path)
    chunk_id = _mint(hub)
    assert _restart(hub, chunk_id, node="build").status_code == 202
    build_node = _detail(hub, chunk_id)["current_node_id"]

    report_lease(hub, chunk_id, epoch=3, seq=2)  # the re-entry mints above the move's own fence
    advanced = hub.client.post(
        f"/api/fleet/chunks/{chunk_id}/completions",
        json={"choice": "fail", "epoch": 3, "runner_id": "r1", "from_node_id": build_node, "artifacts": []},
    )
    assert advanced.json()["outcome"] == "next", advanced.text

    node = hub.client.get(f"/api/fleet/chunks/{chunk_id}/envelope").json()["node"]
    assert node["session"] == SessionMode.RESUME.value


def test_restart_publishes_a_chunk_changed_frame_naming_its_own_cause(tmp_path) -> None:  # type: ignore[no-untyped-def]
    hub = build_hub(tmp_path)
    chunk_id = _mint(hub)
    since = hub.events.latest_id()

    assert _restart(hub, chunk_id).status_code == 202

    events = emitted_events(hub, since=since)
    assert "queue-changed" in [e["event"] for e in events]
    changed = [json.loads(e["data"]) for e in events if e["event"] == "chunk-changed"]
    assert [c["cause"] for c in changed] == ["restarted"]


def test_restart_defaults_a_never_moved_chunk_to_its_graphs_entry_node(tmp_path) -> None:  # type: ignore[no-untyped-def]
    """A chunk that has not moved stands on nowhere, and the entry node is where it would
    have started — the one case the omitted ``--node`` resolves to something derived."""
    hub = build_hub(tmp_path)
    assert hub.client.post("/api/graphs", json={"definition_yaml": _YAML}).status_code == 201
    chunk_id = ingest(hub, [_POINTER])

    assert _restart(hub, chunk_id).status_code == 202

    assert _detail(hub, chunk_id)["current_node_name"] == "plan"


def test_restart_refuses_a_chunk_standing_on_a_node_its_graph_does_not_carry(tmp_path) -> None:  # type: ignore[no-untyped-def]
    """Refused rather than rewound to the entry: the position is real, and defaulting it
    away would silently discard every node the chunk already came through."""
    hub = build_hub(tmp_path)
    chunk_id = _mint(hub, _GATE_YAML)  # entry `build`, no `plan` node
    assert hub.client.post("/api/graphs", json={"definition_yaml": _YAML}).status_code == 201
    build_node = _detail(hub, chunk_id)["current_node_id"]
    advanced = hub.client.post(
        f"/api/fleet/chunks/{chunk_id}/completions",
        json={"choice": "pass", "epoch": 1, "runner_id": "r1", "from_node_id": build_node, "artifacts": []},
    )
    assert advanced.json()["outcome"] == "parked_at_gate", advanced.text
    signoff_node = _detail(hub, chunk_id)["current_node_id"]
    # Re-pin the chunk to a graph carrying no such node — the store's own repin, which the
    # HTTP edit path refuses for a moved chunk.
    pinned = _detail(hub, chunk_id)["graph_id"]
    other = next(g["graph_id"] for g in hub.client.get("/api/graphs").json() if g["graph_id"] != pinned)
    cast(IWriteChunkRecordRepository, hub.services.chunks.record).set_graph(chunk_id, graph_id=other)

    resp = _restart(hub, chunk_id)

    assert resp.status_code == 409, resp.text
    assert signoff_node in resp.json()["detail"]
    assert _detail(hub, chunk_id)["latest_epoch"] == 1  # refused before anything was written


def test_a_restart_mid_hub_node_run_fences_out_that_nodes_exit_transition(tmp_path) -> None:  # type: ignore[no-untyped-def]
    """A hub node reads its epoch, then runs minutes of git/forge work before recording the
    exit. A restart landing inside that window has already re-aimed the chunk, so the exit
    is refused: the move survives, rather than being erased by a write it predates."""
    hub = build_hub(tmp_path)
    chunk_id = _mint(hub)
    plan_node = _detail(hub, chunk_id)["current_node_id"]  # the node the hub run entered at epoch 1

    assert _restart(hub, chunk_id, node="build").status_code == 202
    build_node = _detail(hub, chunk_id)["current_node_id"]

    wrote = cast(IWriteChunkHubExecRepository, hub.services.chunks.hub_exec).record_hub_step_transition(
        chunk_id,
        from_node_id=plan_node,
        to_node_id=build_node,
        choice_name="success",
        epoch=2,  # the run's own hub epoch, minted from the epoch it read before the move
        runner_id="hub",
        transition_id="tr_fenced",
        at=hub.clock.now(),
        artifacts=[],
        release_route=False,
    )

    assert wrote is False
    detail = _detail(hub, chunk_id)
    assert detail["history"] == []
    assert detail["current_node_name"] == "build"  # still where the operator put it


def test_an_uncontested_hub_node_exit_still_records(tmp_path) -> None:  # type: ignore[no-untyped-def]
    """The fence's control: with nothing minted past the run's own epoch, the exit lands."""
    hub = build_hub(tmp_path)
    chunk_id = _mint(hub)
    plan_node = _detail(hub, chunk_id)["current_node_id"]

    wrote = cast(IWriteChunkHubExecRepository, hub.services.chunks.hub_exec).record_hub_step_transition(
        chunk_id,
        from_node_id=plan_node,
        to_node_id="done",
        choice_name="success",
        epoch=2,
        runner_id="hub",
        transition_id="tr_landed",
        at=hub.clock.now(),
        artifacts=[],
        release_route=True,
    )

    assert wrote is True
    assert _detail(hub, chunk_id)["status"] == "done"


# The eager cross-graph move (#371) — `--to-graph`, the counterpart of `migrate`'s intent.
# --------------------------------------------------------------------------- #


def _target_graph(hub) -> str:  # type: ignore[no-untyped-def]
    """Mint the second graph the cross-graph move lands on, and hand back its id."""
    resp = hub.client.post("/api/graphs", json={"definition_yaml": _TARGET_YAML})
    assert resp.status_code == 201, resp.text
    return resp.json()["graph_id"]


def test_a_cross_graph_restart_repins_the_chunk_and_lands_it_by_name(tmp_path) -> None:  # type: ignore[no-untyped-def]
    """The chunk is on the target graph, at the node its own current node's name matched."""
    hub = build_hub(tmp_path)
    chunk_id = _mint(hub)
    assert _restart(hub, chunk_id, node="build").status_code == 202  # stand it on `build`
    target = _target_graph(hub)

    resp = _restart(hub, chunk_id, to_graph=target)

    assert resp.status_code == 202, resp.text
    detail = _detail(hub, chunk_id)
    assert detail["graph_id"] == target
    assert detail["current_node_name"] == "build"
    assert detail["latest_epoch"] == 3


def test_the_move_records_a_migration_fact_and_a_restart_fact(tmp_path) -> None:  # type: ignore[no-untyped-def]
    """Each half is owned by the family that owns its meaning, at one shared epoch."""
    hub = build_hub(tmp_path)
    chunk_id = _mint(hub)
    assert _restart(hub, chunk_id, node="plan").status_code == 202  # a real node to depart from
    source = _detail(hub, chunk_id)["graph_id"]
    target = _target_graph(hub)

    assert _restart(hub, chunk_id, node="audit", to_graph=target).status_code == 202

    detail = _detail(hub, chunk_id)
    migration = detail["migrations"][0]
    assert (migration["from_graph_id"], migration["to_graph_id"], migration["source"]) == (source, target, "restart")
    assert (migration["from_node_name"], migration["landed_node_name"]) == ("plan", "audit")
    restart = detail["restarts"][-1]
    assert (restart["graph_id"], restart["from_graph_id"], restart["epoch"]) == (target, source, 3)
    assert (restart["from_node_name"], restart["to_node_name"]) == ("plan", "audit")
    assert detail["history"] == []  # still no transition was invented for either half


def test_the_restart_is_the_movement_the_chunk_stands_on(tmp_path) -> None:  # type: ignore[no-untyped-def]
    """Both halves land at one instant and one epoch, and the restart is the newer kind."""
    hub = build_hub(tmp_path)
    chunk_id = _mint(hub)
    target = _target_graph(hub)

    assert _restart(hub, chunk_id, node="audit", to_graph=target).status_code == 202

    facts = hub.services.chunks.facts.load_facts(chunk_id)
    assert facts is not None
    migration, restart = facts.migrations[0], facts.restarts[0]
    assert (migration.recorded_at, migration.epoch) == (restart.recorded_at, restart.epoch)
    assert facts.latest_movement() == Movement(MovementKind.RESTART, restart.to_node_id, Executor.RUNNER)
    assert facts.entered_by_restart() is True


def test_the_re_entered_node_stamps_the_target_graphs_declarations(tmp_path) -> None:  # type: ignore[no-untyped-def]
    """AC3, the point of the feature: a stale stamp must not survive the graph move."""
    hub = build_hub(tmp_path)
    chunk_id = _mint(hub)
    assert _restart(hub, chunk_id, node="build").status_code == 202
    before = hub.client.get(f"/api/fleet/chunks/{chunk_id}/envelope").json()["node"]
    assert (before["session_model"], before["session_effort"]) == (["blizzard:advanced"], "high")
    target = _target_graph(hub)

    assert _restart(hub, chunk_id, to_graph=target).status_code == 202

    node = hub.client.get(f"/api/fleet/chunks/{chunk_id}/envelope").json()["node"]
    assert node["session"] == SessionMode.FRESH.value
    assert (node["session_model"], node["session_effort"]) == (["blizzard:basic"], "low")
    assert node["session_compaction_window"] == "40000"


def test_the_claiming_runner_also_gets_the_target_graphs_stamps(tmp_path) -> None:  # type: ignore[no-untyped-def]
    """The claim path resolves the pin itself, so an unleased chunk carries the move too."""
    hub = build_hub(tmp_path)
    assert hub.client.post("/api/graphs", json={"definition_yaml": _YAML}).status_code == 201
    chunk_id = ingest(hub, [_POINTER])
    target = _target_graph(hub)

    assert _restart(hub, chunk_id, node="build", to_graph=target).status_code == 202

    claimed = hub.client.post(
        "/api/fleet/routes",
        json={"chunk_id": chunk_id, "runner_id": "r1", "workspace_id": "w1", "environment_ids": ["env-a"]},
    )
    assert claimed.status_code == 201, claimed.text
    node = claimed.json()["envelope"]["node"]
    assert (node["node_name"], node["session_compaction_window"]) == ("build", "40000")


def test_the_move_clears_a_standing_intended_migration(tmp_path) -> None:  # type: ignore[no-untyped-def]
    """An eager move supersedes a parked one rather than leaving it to fire later."""
    hub = build_hub(tmp_path)
    chunk_id = _mint(hub)
    target = _target_graph(hub)
    patched = hub.client.patch(f"/api/chunks/{chunk_id}", json={"intended_migration": {"to_graph": target}})
    assert patched.status_code == 202, patched.text
    assert _detail(hub, chunk_id)["intended_migration"] is not None

    assert _restart(hub, chunk_id, node="audit", to_graph=target).status_code == 202

    assert _detail(hub, chunk_id)["intended_migration"] is None


def test_the_preempted_workers_completion_cannot_land(tmp_path) -> None:  # type: ignore[no-untyped-def]
    """The move's own migration rides a bumped epoch, so the replay probe never answers it
    MIGRATED; the re-pin then refuses the departed node ahead of the fence, as any migration's
    does. Either way the displaced worker loses work and lands none."""
    hub = build_hub(tmp_path)
    chunk_id = _mint(hub)
    node_id = _detail(hub, chunk_id)["current_node_id"]
    target = _target_graph(hub)

    assert _restart(hub, chunk_id, node="audit", to_graph=target).status_code == 202

    resp = hub.client.post(
        f"/api/fleet/chunks/{chunk_id}/completions",
        json={"choice": "pass", "epoch": 1, "runner_id": "r1", "from_node_id": node_id, "artifacts": []},
    )
    assert resp.json()["outcome"] == "failure", resp.text
    detail = _detail(hub, chunk_id)
    assert (detail["current_node_name"], detail["history"]) == ("audit", [])


def test_a_level_epoch_completion_is_fenced_rather_than_answered_migrated(tmp_path) -> None:  # type: ignore[no-untyped-def]
    """A lease whose `minted` fact is still buffered runner-side lands LEVEL with the move that
    displaces it (`Fenced.out`), so its completion shares the migration's key. Answering that
    MIGRATED would release the environments this re-pin kept — it is fenced like any stale one."""
    hub = build_hub(tmp_path)
    chunk_id = _mint(hub)
    assert _restart(hub, chunk_id, node="build").status_code == 202  # stand it on a real node
    departed = _detail(hub, chunk_id)["current_node_id"]
    target = _target_graph(hub)

    assert _restart(hub, chunk_id, to_graph=target).status_code == 202  # mints epoch 3

    resp = hub.client.post(
        f"/api/fleet/chunks/{chunk_id}/completions",
        json={"choice": "pass", "epoch": 3, "runner_id": "r1", "from_node_id": departed, "artifacts": []},
    )
    assert resp.json()["outcome"] == "failure", resp.text
    detail = _detail(hub, chunk_id)
    assert (detail["graph_id"], detail["current_node_name"], detail["history"]) == (target, "build", [])


def test_a_never_moved_chunk_lands_on_the_targets_entry_node(tmp_path) -> None:  # type: ignore[no-untyped-def]
    """It stands on nowhere, so the entry is where it would have started — on either graph."""
    hub = build_hub(tmp_path)
    assert hub.client.post("/api/graphs", json={"definition_yaml": _YAML}).status_code == 201
    chunk_id = ingest(hub, [_POINTER])
    target = _target_graph(hub)

    assert _restart(hub, chunk_id, to_graph=target).status_code == 202

    assert _detail(hub, chunk_id)["current_node_name"] == "build"


def test_the_move_can_name_its_target_graph_by_name(tmp_path) -> None:  # type: ignore[no-untyped-def]
    """A graph id or a graph name, resolved the way an intended migration's target is."""
    hub = build_hub(tmp_path)
    chunk_id = _mint(hub)
    target = _target_graph(hub)

    assert _restart(hub, chunk_id, node="audit", to_graph="hardened-delivery").status_code == 202

    assert _detail(hub, chunk_id)["graph_id"] == target


def test_the_stores_invariants_hold_over_the_move(tmp_path) -> None:  # type: ignore[no-untyped-def]
    """The re-pin lands with the fact and keeps its route, so neither half reads as torn."""
    hub = build_hub(tmp_path)
    chunk_id = _mint(hub)
    target = _target_graph(hub)

    assert _restart(hub, chunk_id, node="audit", to_graph=target).status_code == 202

    engine = create_engine_from_url(f"sqlite:///{tmp_path / 'hub.db'}")
    assert HubInvariants(engine).run() == []


def test_the_move_refuses_an_unknown_target_graph(tmp_path) -> None:  # type: ignore[no-untyped-def]
    hub = build_hub(tmp_path)
    chunk_id = _mint(hub)

    resp = _restart(hub, chunk_id, to_graph="gr_nope")

    assert resp.status_code == 404, resp.text
    assert _detail(hub, chunk_id)["latest_epoch"] == 1


def test_the_move_refuses_a_retired_target_graph(tmp_path) -> None:  # type: ignore[no-untyped-def]
    """Retired is the operator's brake on new work reaching a graph, and this is new work."""
    hub = build_hub(tmp_path)
    chunk_id = _mint(hub)
    target = _target_graph(hub)
    assert hub.client.post(f"/api/graphs/{target}/retire", json={"by": "ada"}).status_code == 202

    resp = _restart(hub, chunk_id, node="audit", to_graph=target)

    assert resp.status_code == 409, resp.text
    detail = _detail(hub, chunk_id)
    assert (detail["latest_epoch"], detail["graph_id"] != target) == (1, True)


def test_the_move_refuses_a_target_the_chunk_is_already_on(tmp_path) -> None:  # type: ignore[no-untyped-def]
    """A no-op re-pin — plain ``restart`` is the verb for moving within one graph."""
    hub = build_hub(tmp_path)
    chunk_id = _mint(hub)
    pinned = _detail(hub, chunk_id)["graph_id"]

    resp = _restart(hub, chunk_id, node="build", to_graph=pinned)

    assert resp.status_code == 409, resp.text
    assert _detail(hub, chunk_id)["latest_epoch"] == 1


def test_the_move_refuses_a_node_the_target_graph_does_not_carry(tmp_path) -> None:  # type: ignore[no-untyped-def]
    hub = build_hub(tmp_path)
    chunk_id = _mint(hub)
    target = _target_graph(hub)

    resp = _restart(hub, chunk_id, node="plan", to_graph=target)

    assert resp.status_code == 409, resp.text
    assert "plan" in resp.json()["detail"]
    detail = _detail(hub, chunk_id)
    assert (detail["latest_epoch"], detail["graph_id"] == target) == (1, False)


def test_the_move_refuses_an_unnamed_node_whose_name_match_fails(tmp_path) -> None:  # type: ignore[no-untyped-def]
    """Never a silent rewind to the target's entry: the chunk's position is real."""
    hub = build_hub(tmp_path)
    chunk_id = _mint(hub)
    assert _restart(hub, chunk_id, node="plan").status_code == 202  # a real position the target lacks
    target = _target_graph(hub)

    resp = _restart(hub, chunk_id, to_graph=target)

    assert resp.status_code == 409, resp.text
    assert "plan" in resp.json()["detail"]
    detail = _detail(hub, chunk_id)
    assert (detail["latest_epoch"], detail["graph_id"] == target) == (2, False)

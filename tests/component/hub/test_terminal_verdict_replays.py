"""A replay on a chunk that has since ended keeps its recorded outcome, and a first write on it is refused as
terminal (component tier).

The store applies the terminal verdict, read under the chunk's lock, after its replay probe. The hub's own writes
are driven as its callers drive them — the verdict read through ``terminal_under_lock`` on the writing unit."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from blizzard.foundation.migration_source import MigrationSource
from blizzard.hub.domain.chunk.model import DecisionChoice, WorkRef, decide_close_enqueue
from blizzard.hub.domain.chunk.ports.escalations import BounceEscalationOutcome
from blizzard.hub.domain.chunk.ports.fence import EpochAdmission, FenceRefusal
from blizzard.hub.domain.chunk.ports.movement import MigrationWrite
from blizzard.hub.domain.chunk.terminal import terminal_under_lock
from blizzard.hub.domain.kernel.transactions import LockedTransaction
from blizzard.hub.store import schema as s
from blizzard.hub.store.internal.chunk_decisions_store import ChunkDecisionsStore
from blizzard.hub.store.internal.garden_delivery_store import GardenDeliveryStore
from blizzard.hub.store.internal.review_findings_store import ReviewFindingsStore
from tests.support import garden_delivery_world, review_recorder_world
from tests.support.chunk_units import chunk_unit
from tests.support.hub_node_artifacts import record_hub_asset
from tests.support.scaffolding import build_hub, hub_store_connections
from tests.support.write_fence_locks import Chunk

_CLOSE = decide_close_enqueue([WorkRef("default", "1")], lambda _: True)


def _never_found(*_args: object, **_kwargs: object) -> None:
    """A pre-lock replay probe that finds nothing, so a replay meets the store's own probe under the lock."""


def _verdict(chunk: Chunk, tx: LockedTransaction) -> bool:
    return terminal_under_lock(chunk.stores.facts, chunk.chunk_id, transaction=tx)


def _ask(chunk: Chunk, question_id: str) -> tuple[int, str]:
    resp = chunk.hub.client.post(
        "/api/questions",
        json={
            "question_id": question_id,
            "chunk_id": chunk.chunk_id,
            "runner_id": "r1",
            "epoch": 1,
            "question": "?",
            "asked_at": chunk.hub.clock.now().isoformat(),
        },
    )
    return resp.status_code, resp.text


# --- question ---------------------------------------------------------------------------------------------------------


def test_a_question_replayed_on_a_since_stopped_chunk_answers_as_recorded_and_a_fresh_one_is_terminal(
    tmp_path: Path,
) -> None:
    chunk = Chunk(build_hub(tmp_path))
    assert _ask(chunk, "qn_asked")[0] == 201
    chunk.stop()

    assert _ask(chunk, "qn_asked")[0] == 201
    status, body = _ask(chunk, "qn_fresh")
    assert status == 409
    assert "chunk is terminal" in body
    assert chunk.rows(s.questions) == 1


# --- decision ---------------------------------------------------------------------------------------------------------


def test_a_decision_replayed_under_the_lock_on_a_since_stopped_chunk_writes_nothing_and_is_not_refused(
    tmp_path: Path,
) -> None:
    chunk = Chunk(build_hub(tmp_path))
    assert chunk.decide("build", epoch=1)["outcome"] == "parked_at_gate"
    chunk.stop()

    with chunk_unit(hub_store_connections(chunk.hub.engine), [chunk.chunk_id]) as tx:
        replayed = chunk.stores.decisions.record_decision(
            decision_id="dec_replay",
            chunk_id=chunk.chunk_id,
            node_id=chunk.nodes["build"],
            node_name="build",
            epoch=1,
            admission=EpochAdmission.CURRENT,
            terminal=_verdict(chunk, tx),
            choices=[DecisionChoice(name="pass", description="")],
            at=chunk.hub.clock.now(),
            artifacts=[],
            imposed_by_runner_id="r1",
            close_decision=_CLOSE,
            transaction=tx,
        )
        fresh = chunk.stores.decisions.record_decision(
            decision_id="dec_fresh",
            chunk_id=chunk.chunk_id,
            node_id=chunk.nodes["review"],
            node_name="review",
            epoch=1,
            admission=EpochAdmission.CURRENT,
            terminal=_verdict(chunk, tx),
            choices=[DecisionChoice(name="pass", description="")],
            at=chunk.hub.clock.now(),
            artifacts=[],
            imposed_by_runner_id="r1",
            close_decision=_CLOSE,
            transaction=tx,
        )

    assert not isinstance(replayed, FenceRefusal) and replayed.wrote is False
    assert fresh == FenceRefusal.terminal(1)
    assert chunk.rows(s.decisions) == 1


def test_a_decision_resubmitted_past_its_pre_lock_probe_on_a_since_stopped_chunk_keeps_its_route_answer(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    chunk = Chunk(build_hub(tmp_path))
    first = chunk.decide("build", epoch=1)
    chunk.stop()
    before = chunk.decide("build", epoch=1)
    monkeypatch.setattr(ChunkDecisionsStore, "find_decision", _never_found)

    assert chunk.decide("build", epoch=1)["outcome"] == before["outcome"]
    assert first["outcome"] == "parked_at_gate"
    assert chunk.rows(s.decisions) == 1


# --- migration --------------------------------------------------------------------------------------------------------


def _migrate(chunk: Chunk, tx: LockedTransaction, *, from_node: str) -> MigrationWrite | FenceRefusal | None:
    return chunk.stores.movement.record_migration(
        chunk.chunk_id,
        from_node_id=chunk.nodes[from_node],
        from_graph_id="gr_from",
        to_graph_id="gr_to",
        landed_node_id=None,
        choice_name="pass",
        model=None,
        epoch=1,
        admission=EpochAdmission.CURRENT,
        terminal=_verdict(chunk, tx),
        at=chunk.hub.clock.now(),
        artifacts=[],
        source=MigrationSource.AUTHORED_EDGE,
        close_decision=_CLOSE,
        transaction=tx,
    )


def test_a_migration_replayed_on_a_since_stopped_chunk_answers_as_recorded_and_a_fresh_one_is_terminal(
    tmp_path: Path,
) -> None:
    chunk = Chunk(build_hub(tmp_path))
    with chunk_unit(hub_store_connections(chunk.hub.engine), [chunk.chunk_id]) as tx:
        assert isinstance(_migrate(chunk, tx, from_node="build"), MigrationWrite)
    chunk.stop()

    with chunk_unit(hub_store_connections(chunk.hub.engine), [chunk.chunk_id]) as tx:
        replayed = _migrate(chunk, tx, from_node="build")
        fresh = _migrate(chunk, tx, from_node="review")

    assert replayed is None
    assert fresh == FenceRefusal.terminal(1)
    assert chunk.rows(s.chunk_migrations) == 1


# --- hub step, bounce and its escalation ------------------------------------------------------------------------------


def _hub_step(chunk: Chunk, tx: LockedTransaction, *, epoch: int, transition_id: str) -> bool:
    return chunk.stores.hub_exec.record_hub_step_transition(
        chunk.chunk_id,
        from_node_id=chunk.nodes["review"],
        to_node_id=chunk.nodes["build"],
        choice_name="pass",
        epoch=epoch,
        admission=EpochAdmission.ABOVE,
        terminal=_verdict(chunk, tx),
        runner_id="hub",
        transition_id=transition_id,
        at=chunk.hub.clock.now(),
        artifacts=[],
        release_route=False,
        close_decision=_CLOSE,
        transaction=tx,
    ).wrote


def test_a_hub_step_replayed_on_a_since_stopped_chunk_writes_nothing_twice_and_a_fresh_one_is_fenced(
    tmp_path: Path,
) -> None:
    chunk = Chunk(build_hub(tmp_path))
    with chunk_unit(hub_store_connections(chunk.hub.engine), [chunk.chunk_id]) as tx:
        assert _hub_step(chunk, tx, epoch=2, transition_id="tr_hub_1") is True
    chunk.stop()
    transitions = chunk.rows(s.transitions)

    with chunk_unit(hub_store_connections(chunk.hub.engine), [chunk.chunk_id]) as tx:
        assert _verdict(chunk, tx) is True
        assert _hub_step(chunk, tx, epoch=2, transition_id="tr_hub_replay") is False
        assert _hub_step(chunk, tx, epoch=3, transition_id="tr_hub_fresh") is False

    assert chunk.rows(s.transitions) == transitions
    assert chunk.rows(s.lease_facts) == 2  # the runner's lease and the first hub step's; nothing after the stop


def test_a_bounce_and_its_escalation_replayed_on_a_since_stopped_chunk_keep_their_recorded_outcomes(
    tmp_path: Path,
) -> None:
    chunk = Chunk(build_hub(tmp_path))

    def bounce(tx: LockedTransaction, epoch: int) -> bool:
        return chunk.stores.escalations.record_bounce(
            chunk.chunk_id,
            epoch=epoch,
            admission=EpochAdmission.ABOVE,
            terminal=_verdict(chunk, tx),
            cause="poll-timeout",
            envelope="{}",
            at=chunk.hub.clock.now(),
            transaction=tx,
        )

    def escalate(tx: LockedTransaction, epoch: int) -> BounceEscalationOutcome:
        return chunk.stores.escalations.record_bounce_escalation(
            chunk.chunk_id,
            epoch=epoch,
            admission=EpochAdmission.ABOVE,
            terminal=_verdict(chunk, tx),
            runner_id="hub",
            takeover_command="",
            at=chunk.hub.clock.now(),
            cause="bounce-cap",
            detail="crossed",
            transaction=tx,
        )

    with chunk_unit(hub_store_connections(chunk.hub.engine), [chunk.chunk_id]) as tx:
        assert bounce(tx, 2) is True
        assert escalate(tx, 2) is BounceEscalationOutcome.RECORDED
    chunk.stop()

    with chunk_unit(hub_store_connections(chunk.hub.engine), [chunk.chunk_id]) as tx:
        assert bounce(tx, 2) is False
        assert escalate(tx, 2) is BounceEscalationOutcome.ALREADY_RECORDED
        assert bounce(tx, 3) is False
        assert escalate(tx, 3) is BounceEscalationOutcome.REFUSED

    assert chunk.rows(s.chunk_bounces) == 1
    assert chunk.rows(s.escalations) == 1


# --- hub artifact -----------------------------------------------------------------------------------------------------


def test_a_hub_artifact_replayed_on_a_since_stopped_chunk_stays_recorded_once_and_a_fresh_one_is_fenced(
    tmp_path: Path,
) -> None:
    chunk = Chunk(build_hub(tmp_path))

    def record(name: str) -> bool:
        return record_hub_asset(
            chunk.hub, chunk.chunk_id, node_id=chunk.nodes["build"], node_name="build", epoch=1, name=name, content="c"
        )

    assert record("hub-log.build") is True
    chunk.stop()
    artifacts = chunk.rows(s.artifacts)

    assert record("hub-log.build") is False
    assert record("hub-log.fresh") is False
    assert chunk.rows(s.artifacts) == artifacts
    assert chunk.stores.artifacts.has_hub_artifact(
        chunk.chunk_id, node_id=chunk.nodes["build"], epoch=1, name="hub-log.build"
    )


# --- garden and review delivery ---------------------------------------------------------------------------------------


def test_a_garden_delivery_replayed_under_the_lock_on_a_since_stopped_chunk_answers_as_recorded(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    hub = build_hub(tmp_path)
    garden_delivery_world.seed_scope(hub, garden_delivery_world.SCOPE)
    chunk_id = garden_delivery_world.seed_chunk(hub)
    garden_delivery_world.record_artifact(
        hub,
        chunk_id,
        name="delta",
        content=garden_delivery_world.garden_delta(findings=[garden_delivery_world.add_op()]),
    )
    assert garden_delivery_world.deliver(hub, chunk_id, delta=["delta"]).outcome == "recorded"
    assert hub.client.post(f"/api/chunks/{chunk_id}/stop", json={"by": "operator"}).status_code == 202
    monkeypatch.setattr(GardenDeliveryStore, "already_delivered", _never_found)

    assert garden_delivery_world.deliver(hub, chunk_id, delta=["delta"]).outcome == "recorded"
    assert garden_delivery_world.finding_count(hub) == 1


def test_a_review_delivery_replayed_under_the_lock_on_a_since_stopped_chunk_answers_as_recorded(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    hub = build_hub(tmp_path)
    chunk_id = review_recorder_world.seed_chunk(hub)
    deferred = {
        "ref": "F1",
        "disposition": "deferred",
        "severity": "should-fix",
        "scope": "blizzard",
        "class": "correctness",
        "locus": "a.py:1",
        "summary": "s",
    }
    review_recorder_world.record_artifact(hub, chunk_id, content=json.dumps({"entries": [deferred]}))
    assert review_recorder_world.deliver(hub, chunk_id).outcome == "recorded"
    assert hub.client.post(f"/api/chunks/{chunk_id}/stop", json={"by": "operator"}).status_code == 202
    monkeypatch.setattr(ReviewFindingsStore, "already_delivered", _never_found)

    assert review_recorder_world.deliver(hub, chunk_id).outcome == "recorded"
    assert review_recorder_world.finding_count(hub) == 1

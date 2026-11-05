"""The escalating close and the answer wake, each one transaction (unit tier).

Pins: an escalated closure buffers its event and its ``escalation.recorded`` fact in the
closure's own transaction, in that order, and a raise inside it leaves none of them; the
answer wake's park resume rides the woken pid's record, so a raise leaves neither, and a
replayed ``ON_ANSWER`` past the record wakes nothing. Each wake names its own crash points."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import Column, MetaData, String, Table

from blizzard.foundation.chunk_status import ChunkStatus
from blizzard.foundation.crash import CrashPoint
from blizzard.foundation.escalation_causes import EscalationCause
from blizzard.foundation.fact_kinds import ANSWER_DELIVERED, ESCALATION_RECORDED, EVENT_RECORDED
from blizzard.foundation.leases import LeaseClosureReason
from blizzard.runner.harness.adapter import WorkerHandle
from blizzard.runner.harness.identity import CLAUDE_CODE_HARNESS_ID, SessionReference
from blizzard.runner.harness.registry import UnknownHarnessError
from blizzard.runner.hub.client import HubQuestion
from blizzard.runner.leases.model import NewLease
from blizzard.runner.leases.record import ClosureFact
from blizzard.runner.lifecycle.attempt import Attempt
from blizzard.runner.lifecycle.dormant import DormantSession
from blizzard.runner.lifecycle.session import SkippedHarness
from blizzard.runner.loop.context import LoopConfig
from blizzard.runner.loop.steps import Advance
from blizzard.runner.node_steps.chunk_state import ChunkState
from blizzard.runner.store.internal import lease_liveness_store, lease_record_store
from tests.support.runner_fakes import (
    FIXTURE_RUNNER_ID,
    FakeHarness,
    FakeHub,
    FakeProbe,
    FakeProvider,
    SqlAlchemyRunnerStore,
    make_context,
    make_store,
    serving,
)

pytestmark = pytest.mark.unit

_NOW = datetime(2026, 7, 13, 12, 0, 0, tzinfo=UTC)
_HANDLE = WorkerHandle(session_id="sess-a", pid=100, process_start_time="start-100", pgid=100)
_SESSION = SessionReference(CLAUDE_CODE_HARNESS_ID, "sess-a")


def _store(tmp_path: Path) -> SqlAlchemyRunnerStore:
    return make_store(f"sqlite:///{tmp_path / 'runner.db'}")


def _seed_lease(store: SqlAlchemyRunnerStore, *, session: SessionReference | None = _SESSION) -> None:
    store.record_lease(
        NewLease(
            lease_id="lease_1",
            chunk_id="ch_1",
            graph_id="gr_1",
            node_id="nd_build",
            node_name="build",
            epoch=1,
            retries_max=0,
            created_at=_NOW,
        )
    )
    if session is not None:
        store.record_spawn("lease_1", pid=100, process_start_time="start-100", session=session, spawned_at=_NOW)
    store.record_binding(chunk_id="ch_1", environment_id="e1", workdir="/ws/e1", bound_at=_NOW, workspace_id="ws1")


def _ctx(store: SqlAlchemyRunnerStore, hub: FakeHub | None = None, harness: FakeHarness | None = None):
    if hub is None:
        hub = FakeHub()
        hub.chunks["ch_1"] = ChunkState(
            chunk_id="ch_1", status=ChunkStatus.RUNNING, latest_epoch=1, route_runner_id=FIXTURE_RUNNER_ID
        )
    return make_context(
        store,
        hub=hub,
        provider=FakeProvider({"e1": "/ws/e1"}),
        harness=harness or FakeHarness(handle=_HANDLE, verdict="pass"),
        probe=FakeProbe(),
        config=LoopConfig(
            runner_name="r1",
            runner_id=FIXTURE_RUNNER_ID,
            bindings=serving(1),
            agents=1,
            runner_dir="/tmp/runner-dir",
        ),
    )


def _spy_closures(store: SqlAlchemyRunnerStore, monkeypatch: pytest.MonkeyPatch) -> list[list[str]]:
    """Every ``record_closure`` call's fact kinds, in call order."""
    calls: list[list[str]] = []
    real = store.record_closure

    def spy(**kwargs: Any) -> list[int]:
        calls.append([fact.kind for fact in kwargs.get("facts", ())])
        return real(**kwargs)

    monkeypatch.setattr(store, "record_closure", spy)
    return calls


def _assert_one_escalating_closure(store: SqlAlchemyRunnerStore, calls: list[list[str]]) -> dict[str, object]:
    """The lease closed once, carrying its event then its escalation; returns the escalation's payload."""
    assert calls == [[EVENT_RECORDED, ESCALATION_RECORDED]]
    assert store.active_lease("lease_1") is None
    rows = [f for f in store.pending_outbound() if f.lease_id == "lease_1"]
    kinds = [f.kind for f in rows]
    assert kinds[-2:] == [EVENT_RECORDED, ESCALATION_RECORDED]
    assert rows[-1].seq == rows[-2].seq + 1
    return json.loads(rows[-1].payload)


# --- record_closure: the closure and every fact it carries, one transaction ---


def test_record_closure_lands_the_closure_and_both_facts_in_order(tmp_path: Path):
    store = _store(tmp_path)
    _seed_lease(store)

    seqs = store.record_closure(
        lease_id="lease_1",
        chunk_id="ch_1",
        node_id="nd_build",
        reason="escalated",
        closed_at=_NOW,
        facts=[ClosureFact(kind=EVENT_RECORDED, payload="{}"), ClosureFact(kind=ESCALATION_RECORDED, payload="{}")],
        escalation_cause=str(EscalationCause.RETRIES_EXHAUSTED),
    )

    assert store.active_lease("lease_1") is None
    rows = store.pending_outbound()
    assert [(f.seq, f.kind) for f in rows] == list(zip(seqs, [EVENT_RECORDED, ESCALATION_RECORDED], strict=True))
    assert seqs == sorted(seqs)


def test_a_raise_inside_record_closure_leaves_neither_the_closure_nor_any_fact(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    store = _store(tmp_path)
    _seed_lease(store)
    real = lease_record_store.insert_outbox_row

    def failing(*args: Any, **values: Any) -> int:
        if values["kind"] == ESCALATION_RECORDED:
            raise RuntimeError("buffer write failed")
        return real(*args, **values)

    monkeypatch.setattr(lease_record_store, "insert_outbox_row", failing)

    with pytest.raises(RuntimeError, match="buffer write failed"):
        store.record_closure(
            lease_id="lease_1",
            chunk_id="ch_1",
            node_id="nd_build",
            reason="escalated",
            closed_at=_NOW,
            facts=[
                ClosureFact(kind=EVENT_RECORDED, payload="{}"),
                ClosureFact(kind=ESCALATION_RECORDED, payload="{}"),
            ],
        )

    assert store.active_lease("lease_1") is not None
    assert store.pending_outbound() == []


# --- the escalation sites, each one escalating close ---


def test_retries_exhausted_buffers_its_escalation_inside_the_closure(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    store = _store(tmp_path)
    _seed_lease(store)
    calls = _spy_closures(store, monkeypatch)
    lease = store.active_lease("lease_1")
    assert lease is not None

    Attempt(_ctx(store), lease, braked=False).fail(reason=LeaseClosureReason.REAPED, via="test")

    payload = _assert_one_escalating_closure(store, calls)
    assert payload["cause"] == "retries-exhausted"
    assert "--resume sess-a" in str(payload["takeover_command"])
    assert payload["wrapped_takeover_command"] == "blizzard runner takeover ch_1 --dir /tmp/runner-dir --runner r1"


def test_owner_unresolvable_buffers_its_escalation_inside_the_closure(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    store = _store(tmp_path)
    ghost = SessionReference("ghost-harness", "sess-a")
    _seed_lease(store, session=ghost)
    calls = _spy_closures(store, monkeypatch)
    lease = store.active_lease("lease_1")
    assert lease is not None

    Attempt(_ctx(store), lease, braked=False).escalate_owner_unresolvable(
        session=ghost, exc=UnknownHarnessError("ghost-harness", ()), via="test"
    )

    payload = _assert_one_escalating_closure(store, calls)
    assert payload["cause"] == "owner-unresolvable"
    # The owner cannot be resolved, so no takeover command composes — the escalation still lands.
    assert (payload["takeover_command"], payload["wrapped_takeover_command"]) == ("", "")
    assert store.resume_intent_lease_ids() == set()


def test_no_acceptable_harness_buffers_its_escalation_inside_the_closure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    store = _store(tmp_path)
    _seed_lease(store, session=None)
    calls = _spy_closures(store, monkeypatch)
    lease = store.active_lease("lease_1")
    assert lease is not None

    Attempt(_ctx(store), lease, braked=False).escalate_no_acceptable_harness(
        attempted=["ghost-harness"], skipped=[SkippedHarness("ghost-harness", "unknown")], via="test"
    )

    payload = _assert_one_escalating_closure(store, calls)
    assert payload["cause"] == "no-acceptable-harness"
    assert (payload["takeover_command"], payload["wrapped_takeover_command"]) == ("", "")


# --- record_spawn: the woken pid and the answered park, one transaction ---


def _seed_parked(store: SqlAlchemyRunnerStore) -> None:
    _seed_lease(store)
    store.record_ask(
        lease_id="lease_1",
        chunk_id="ch_1",
        question_id="qn_1",
        question="Q",
        options=[],
        session=_SESSION,
        asked_at=_NOW,
    )
    store.record_park(lease_id="lease_1", chunk_id="ch_1", question_id="qn_1", parked_at=_NOW)


def test_record_spawn_with_a_park_resume_lands_the_pid_and_the_resume_together(tmp_path: Path):
    store = _store(tmp_path)
    _seed_parked(store)

    store.record_spawn(
        "lease_1",
        pid=4321,
        process_start_time="start-4321",
        session=_SESSION,
        spawned_at=_NOW,
        park_resume_question_id="qn_1",
    )

    assert store.open_park("lease_1") is None
    lease = store.active_lease("lease_1")
    assert lease is not None and lease.pid == 4321


def test_a_raise_inside_record_spawn_leaves_neither_the_pid_nor_the_park_resume(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    store = _store(tmp_path)
    _seed_parked(store)
    # A table the store never created: the park-resume insert fails inside the transaction.
    missing = Table(
        "no_such_park_resumes",
        MetaData(),
        Column("lease_id", String),
        Column("question_id", String),
        Column("resumed_at", String),
    )
    monkeypatch.setattr(lease_liveness_store, "park_resumes", missing)

    with pytest.raises(Exception, match="no_such_park_resumes"):
        store.record_spawn(
            "lease_1",
            pid=4321,
            process_start_time="start-4321",
            session=_SESSION,
            spawned_at=_NOW,
            park_resume_question_id="qn_1",
        )

    park = store.open_park("lease_1")
    assert park is not None and park.question_id == "qn_1"
    lease = store.active_lease("lease_1")
    assert lease is not None and lease.pid == 100


# --- on_answer ---


def _answered_hub() -> FakeHub:
    hub = FakeHub()
    hub.questions["qn_1"] = HubQuestion(
        question_id="qn_1",
        chunk_id="ch_1",
        runner_id="r1",
        epoch=1,
        question="Q",
        asked_at="t",
        answered=True,
        answer="rest",
        answered_by="alice",
        answered_at="t2",
    )
    return hub


def _reached_points(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    reached: list[str] = []

    def record(self: CrashPoint) -> None:
        reached.append(self.name)

    monkeypatch.setattr(CrashPoint, "reached", record)
    return reached


def test_the_answer_wake_reaches_its_own_points_and_closes_the_park(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    store = _store(tmp_path)
    _seed_parked(store)
    harness = FakeHarness(handle=_HANDLE, verdict="pass")
    harness.resume_pid = 4321
    reached = _reached_points(monkeypatch)

    Advance(_ctx(store, hub=_answered_hub(), harness=harness)).run()

    assert len(harness.resumed) == 1
    assert store.open_park("lease_1") is None
    wake = [p for p in reached if ".wake." in p]
    assert wake == [
        "answer.wake.after-boundary-record.before-launch",
        "answer.wake.after-launch.before-record",
        "answer.wake.after-record",
    ]
    assert [f for f in store.pending_outbound() if f.kind == ANSWER_DELIVERED]


def test_on_answer_replayed_after_the_record_finds_the_park_closed_and_does_not_wake(tmp_path: Path):
    """The crash-after-record state: the woken pid and the park resume are durable, the
    ``answer.delivered`` fact is not. The next pass reads no open park and wakes nothing."""
    store = _store(tmp_path)
    _seed_parked(store)
    store.record_spawn(
        "lease_1",
        pid=4321,
        process_start_time="start-4321",
        session=_SESSION,
        spawned_at=_NOW,
        park_resume_question_id="qn_1",
    )
    harness = FakeHarness(handle=_HANDLE, verdict="pass")
    ctx = _ctx(store, hub=_answered_hub(), harness=harness)
    lease = store.active_lease("lease_1")
    assert lease is not None

    DormantSession(ctx, lease).on_answer()

    assert harness.resumed == []

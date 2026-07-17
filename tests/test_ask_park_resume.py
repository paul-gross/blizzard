"""Ask-and-exit park/resume in the reconciliation loop (unit tier) — MVP criterion 7.

Drives the loop steps directly against a real tmp runner store with fakes at the seams
(``bzh:steppable-loop``) to pin the runner half of the protocol ([ask-answer.md]):

* an exited worker holding an unforwarded ask **parks** — ADVANCE forwards the
  ``question.asked`` up the outbound buffer, records the park fact, and elicits **no**
  verdict and consumes **no** retry;
* a parked lease's reap clock is **stopped** — REAP skips it even when its recorded pid
  reads alive and its heartbeat is long stale (the design's "no live lease while parked");
* the answer's arrival **resumes the dormant session** — same lease, same session
   — records the park-resume and ``answer.delivered``, and the lease reads live
  again;
* the park is not repeated once forwarded, so the verdict is never elicited on the ask.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from blizzard.foundation.clock import FixedClock
from blizzard.runner.domain.leases import HEARTBEAT_STALENESS_THRESHOLD
from blizzard.runner.harness.adapter import WorkerHandle
from blizzard.runner.loop.steps import advance, reap
from blizzard.runner.store.repository import NewLease
from blizzard.wire.facts import ANSWER_DELIVERED, QUESTION_ASKED
from blizzard.wire.question import QuestionView
from tests.runner_fakes import FakeHarness, FakeHub, FakeProbe, FakeProvider, make_context, make_store

pytestmark = pytest.mark.unit

_NOW = datetime(2026, 7, 13, 12, 0, 0, tzinfo=UTC)
_HANDLE_PID = 100
_HANDLE_START = "start-100"
_HANDLE = WorkerHandle(session_id="sess-a", pid=_HANDLE_PID, process_start_time=_HANDLE_START)


def _store(tmp_path):  # type: ignore[no-untyped-def]
    return make_store(f"sqlite:///{tmp_path / 'runner.db'}")


def _seed_exited_lease(store):  # type: ignore[no-untyped-def]
    """A build lease spawned into env e1; the worker has exited (probe reports it dead)."""
    store.record_lease(
        NewLease(
            lease_id="lease_1",
            chunk_id="ch_1",
            graph_id="gr_1",
            node_id="nd_build",
            node_name="build",
            epoch=1,
            runner_id="r1",
            retries_max=2,
            created_at=_NOW,
        )
    )
    store.record_spawn(
        "lease_1", pid=_HANDLE_PID, process_start_time=_HANDLE_START, session_id="sess-a", spawned_at=_NOW
    )
    store.record_binding(chunk_id="ch_1", environment_id="e1", workdir="/ws/e1", bound_at=_NOW)


def _ctx(store, *, hub=None, probe=None, clock=None):  # type: ignore[no-untyped-def]
    return make_context(
        store,
        hub=hub or FakeHub(),
        provider=FakeProvider({"e1": "/ws/e1"}),
        harness=FakeHarness(handle=_HANDLE, verdict="pass"),
        probe=probe or FakeProbe(),
        clock=clock or FixedClock(_NOW),
    )


def _open_question(question_id="qn_1") -> QuestionView:  # type: ignore[no-untyped-def]
    return QuestionView(
        question_id=question_id, chunk_id="ch_1", runner_id="r1", epoch=1, question="Which API?", asked_at="t"
    )


def _answered_question(question_id="qn_1") -> QuestionView:  # type: ignore[no-untyped-def]
    return QuestionView(
        question_id=question_id,
        chunk_id="ch_1",
        runner_id="r1",
        epoch=1,
        question="Which API?",
        asked_at="t",
        answered=True,
        answer="rest",
        answered_by="alice",
        answered_at="t2",
    )


def test_exited_worker_with_open_ask_parks_without_a_verdict(tmp_path):  # type: ignore[no-untyped-def]
    store = _store(tmp_path)
    _seed_exited_lease(store)
    store.record_ask(
        lease_id="lease_1",
        chunk_id="ch_1",
        question_id="qn_1",
        question="Which API?",
        options=["rest", "graphql"],
        session_id="sess-a",
        asked_at=_NOW,
    )
    harness = FakeHarness(handle=_HANDLE, verdict="pass")
    ctx = make_context(
        store, hub=FakeHub(), provider=FakeProvider({"e1": "/ws/e1"}), harness=harness, probe=FakeProbe()
    )

    advance(ctx)

    # Parked: the reap clock is stopped and the chunk derives waiting_on_human (at the hub).
    assert store.parked_lease_ids() == {"lease_1"}
    # The question was forwarded up the outbound buffer (store-and-forward, D-069).
    buffered = [f for f in store.pending_outbound() if f.kind == QUESTION_ASKED]
    assert len(buffered) == 1
    assert '"question_id": "qn_1"' in buffered[0].payload
    # No verdict elicited and no completion buffered — a park is not a judgement.
    assert harness.judged == []
    assert store.pending_submission_lease_ids() == set()


def test_park_is_not_repeated_and_never_elicits_a_verdict(tmp_path):  # type: ignore[no-untyped-def]
    store = _store(tmp_path)
    _seed_exited_lease(store)
    store.record_ask(
        lease_id="lease_1",
        chunk_id="ch_1",
        question_id="qn_1",
        question="Which API?",
        options=[],
        session_id="sess-a",
        asked_at=_NOW,
    )
    hub = FakeHub()
    hub.questions["qn_1"] = _open_question()  # the answer poll on the next tick — still open
    harness = FakeHarness(handle=_HANDLE, verdict="pass")
    ctx = make_context(store, hub=hub, provider=FakeProvider({"e1": "/ws/e1"}), harness=harness, probe=FakeProbe())

    advance(ctx)  # parks
    advance(ctx)  # still parked, answer not yet in — a no-op poll

    assert store.parked_lease_ids() == {"lease_1"}
    assert len([f for f in store.pending_outbound() if f.kind == QUESTION_ASKED]) == 1  # not re-forwarded
    assert harness.judged == []  # never elicited a verdict on the ask


def test_parked_lease_is_not_reaped_though_pid_reads_alive_and_stale(tmp_path):  # type: ignore[no-untyped-def]
    store = _store(tmp_path)
    _seed_exited_lease(store)
    store.record_ask(
        lease_id="lease_1",
        chunk_id="ch_1",
        question_id="qn_1",
        question="Q",
        options=[],
        session_id="sess-a",
        asked_at=_NOW,
    )
    store.record_park(lease_id="lease_1", chunk_id="ch_1", question_id="qn_1", parked_at=_NOW)

    # The recorded pid reads ALIVE and the heartbeat is far past the staleness threshold —
    # without the park guard REAP would reap it as stalled. The park stops the clock.
    later = _NOW + HEARTBEAT_STALENESS_THRESHOLD + timedelta(hours=1)
    probe = FakeProbe(alive={(_HANDLE_PID, _HANDLE_START)})
    ctx = _ctx(store, probe=probe, clock=FixedClock(later))

    reap(ctx)

    assert store.active_lease("lease_1") is not None  # not closed
    assert probe.killed == []  # not killed
    assert [f for f in store.pending_outbound() if f.kind == "escalation.recorded"] == []


def test_answer_resumes_the_dormant_session_under_the_same_lease(tmp_path):  # type: ignore[no-untyped-def]
    store = _store(tmp_path)
    _seed_exited_lease(store)
    store.record_ask(
        lease_id="lease_1",
        chunk_id="ch_1",
        question_id="qn_1",
        question="Q",
        options=[],
        session_id="sess-a",
        asked_at=_NOW,
    )
    store.record_park(lease_id="lease_1", chunk_id="ch_1", question_id="qn_1", parked_at=_NOW)

    hub = FakeHub()
    hub.questions["qn_1"] = _answered_question()
    harness = FakeHarness(handle=_HANDLE, verdict="pass")
    harness.resume_pid = 4321
    ctx = make_context(store, hub=hub, provider=FakeProvider({"e1": "/ws/e1"}), harness=harness, probe=FakeProbe())

    advance(ctx)

    # The dormant session was resumed around the answer — same session id, same lease.
    assert harness.resumed == [("/ws/e1", "sess-a", "# Answer from alice. Continue.\nrest")]
    # The park is closed and the lease reads live again (a fresh pid recorded).
    assert store.parked_lease_ids() == set()
    resumed_lease = store.active_lease("lease_1")
    assert resumed_lease is not None and resumed_lease.pid == 4321
    # answer.delivered was buffered up to the hub (board detail).
    assert [f for f in store.pending_outbound() if f.kind == ANSWER_DELIVERED]

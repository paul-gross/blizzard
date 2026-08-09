"""Ask-and-exit park/resume in the reconciliation loop (unit tier) — MVP criterion 7.

Pins: an exited worker holding an unforwarded ask parks (no verdict, no retry consumed);
a parked lease's reap clock stops; the answer resumes the dormant session under the same
lease; the park is not repeated once forwarded.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from blizzard.foundation.clock import FixedClock
from blizzard.hub.domain.work import ChunkStatus
from blizzard.runner.domain.leases import HEARTBEAT_STALENESS_THRESHOLD
from blizzard.runner.harness.adapter import WorkerHandle
from blizzard.runner.loop.steps import Advance, Pull, Reap
from blizzard.runner.store.repository import NewLease
from blizzard.wire.chunk import ChunkDetail
from blizzard.wire.facts import ANSWER_DELIVERED, QUESTION_ASKED
from blizzard.wire.question import QuestionView
from tests.runner_fakes import FakeHarness, FakeHub, FakeProbe, FakeProvider, make_context, make_envelope, make_store

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

    Advance(ctx).run()

    # Parked: the reap clock is stopped.
    assert store.parked_lease_ids() == {"lease_1"}
    # The question was forwarded up the outbound buffer (store-and-forward).
    buffered = [f for f in store.pending_outbound() if f.kind == QUESTION_ASKED]
    assert len(buffered) == 1
    assert '"question_id": "qn_1"' in buffered[0].payload
    # No verdict elicited and no completion buffered — a park is not a judgement.
    assert harness.judged == []
    assert store.pending_submission_lease_ids() == set()


def test_ask_during_judgement_parks_instead_of_failing(tmp_path):  # type: ignore[no-untyped-def]
    """No ask is open when the worker exits — the pre-elicitation check in
    `_advance_exited_worker` finds nothing — but the worker asks instead of returning a
    verdict *during* the judgement elicitation itself. `Judgement.run`'s own verdict-less
    branch must re-check and park, not burn a retry on a verdict that was never coming."""
    store = _store(tmp_path)
    _seed_exited_lease(store)

    def _ask_mid_judgement() -> None:
        store.record_ask(
            lease_id="lease_1",
            chunk_id="ch_1",
            question_id="qn_1",
            question="Which API?",
            options=["rest", "graphql"],
            session_id="sess-a",
            asked_at=_NOW,
        )

    hub = FakeHub()
    hub.envelopes["ch_1"] = make_envelope("ch_1", "build", node_id="nd_build", choices=[("pass", "meets criteria")])
    harness = FakeHarness(handle=_HANDLE, verdict=None, judge_side_effect=_ask_mid_judgement)
    ctx = make_context(store, hub=hub, provider=FakeProvider({"e1": "/ws/e1"}), harness=harness, probe=FakeProbe())

    Advance(ctx).run()

    # Parked, not failed: no retry consumed, same lease still active.
    assert store.parked_lease_ids() == {"lease_1"}
    assert store.attempt_count("ch_1", "nd_build") == 1
    lease = store.active_lease_for_chunk("ch_1")
    assert lease is not None and lease.lease_id == "lease_1"
    # The question was forwarded up the outbound buffer (store-and-forward).
    buffered = [f for f in store.pending_outbound() if f.kind == QUESTION_ASKED]
    assert len(buffered) == 1
    assert '"question_id": "qn_1"' in buffered[0].payload
    assert store.pending_submission_lease_ids() == set()  # no completion buffered


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

    Advance(ctx).run()  # parks
    Advance(ctx).run()  # still parked, answer not yet in — a no-op poll

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

    Reap(ctx).run()

    assert store.active_lease("lease_1") is not None  # not closed
    assert probe.killed == []  # not killed
    assert [f for f in store.pending_outbound() if f.kind == "escalation.recorded"] == []


def test_ask_forwards_correctly_while_a_pause_park_exists(tmp_path):  # type: ignore[no-untyped-def]
    """issue #46: a pause-park on another lease must not disturb `unforwarded_ask`'s
    predicate — a NULL-poisoned NOT IN would silently stop forwarding fleet-wide if
    pause-parks shared the `park_facts` table; they live in their own table instead."""
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
    # A pause-park on a *different* lease — proves the predicate is untouched by the
    # separate table's presence, not merely untouched because it targets this lease.
    store.record_pause_park(lease_id="lease_other", chunk_id="ch_other", parked_at=_NOW)

    harness = FakeHarness(handle=_HANDLE, verdict="pass")
    ctx = make_context(
        store, hub=FakeHub(), provider=FakeProvider({"e1": "/ws/e1"}), harness=harness, probe=FakeProbe()
    )

    Advance(ctx).run()

    # The ask-park landed on lease_1 alongside the pre-existing pause-park on the
    # unrelated lease_other — parked_lease_ids() is their union (§1.3).
    assert store.ask_parked_lease_ids() == {"lease_1"}
    assert store.pause_parked_lease_ids() == {"lease_other"}
    assert store.parked_lease_ids() == {"lease_1", "lease_other"}
    buffered = [f for f in store.pending_outbound() if f.kind == QUESTION_ASKED]
    assert len(buffered) == 1
    assert '"question_id": "qn_1"' in buffered[0].payload
    assert harness.judged == []


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

    Advance(ctx).run()

    # The dormant session was resumed around the answer — same session id, same lease.
    assert harness.resumed == [("/ws/e1", "sess-a", "# Answer from alice. Continue.\nrest")]
    # The park is closed and the lease reads live again (a fresh pid recorded).
    assert store.parked_lease_ids() == set()
    resumed_lease = store.active_lease("lease_1")
    assert resumed_lease is not None and resumed_lease.pid == 4321
    # answer.delivered was buffered up to the hub.
    assert [f for f in store.pending_outbound() if f.kind == ANSWER_DELIVERED]


def test_worker_resumed_after_a_park_past_the_threshold_survives_the_next_reap(tmp_path):  # type: ignore[no-untyped-def]
    """Issue #150: a worker answered past ``HEARTBEAT_STALENESS_THRESHOLD`` must
    survive the very next REAP tick after resuming."""
    store = _store(tmp_path)
    _seed_exited_lease(store)
    store.record_heartbeat(lease_id="lease_1", beat_at=_NOW)
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

    answered_at = _NOW + HEARTBEAT_STALENESS_THRESHOLD + timedelta(hours=1)
    hub = FakeHub()
    hub.questions["qn_1"] = _answered_question()
    harness = FakeHarness(handle=_HANDLE, verdict="pass")
    harness.resume_pid = 4321
    resumed_probe = FakeProbe(alive={(4321, _HANDLE_START)})
    ctx = make_context(
        store,
        hub=hub,
        provider=FakeProvider({"e1": "/ws/e1"}),
        harness=harness,
        probe=resumed_probe,
        clock=FixedClock(answered_at),
    )

    Advance(ctx).run()
    resumed = store.active_lease("lease_1")
    assert resumed is not None and resumed.pid == 4321

    # The next tick, half a minute into the resumed worker's first inference turn.
    Reap(
        make_context(
            store,
            hub=hub,
            provider=FakeProvider({"e1": "/ws/e1"}),
            harness=harness,
            probe=resumed_probe,
            clock=FixedClock(answered_at + timedelta(seconds=30)),
        )
    ).run()

    assert store.active_lease("lease_1") is not None  # not reaped
    assert resumed_probe.killed == []
    assert [f for f in store.pending_outbound() if f.kind == "escalation.recorded"] == []


def test_a_chunk_stopped_hub_side_while_parked_on_an_ask_retires_the_open_park(tmp_path):  # type: ignore[no-untyped-def]
    """blizzard#202: the operator stops a chunk instead of answering its ask. The ask
    must not read open forever after the chunk it belonged to is gone.
    """
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
    store.record_park(lease_id="lease_1", chunk_id="ch_1", question_id="qn_1", parked_at=_NOW)

    hub = FakeHub()
    hub.chunks["ch_1"] = ChunkDetail(
        chunk_id="ch_1",
        graph_id="gr_1",
        status=ChunkStatus.STOPPED,
        current_node_id="nd_build",
        latest_epoch=1,
        route=None,
    )
    ctx = _ctx(store, hub=hub)

    Pull(ctx).run()

    # The lease is closed, the ask-park is retired, and the environment is freed — no
    # facet of this abandoned, ask-parked chunk lingers as "open" or "held".
    assert store.active_lease("lease_1") is None
    assert store.parked_lease_ids() == set()
    assert store.open_asks() == []
    assert store.held_environment_ids() == []

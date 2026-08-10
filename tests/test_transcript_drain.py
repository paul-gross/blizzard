"""The transcript lane's drain (component tier, issue #246) — break-on-error, ack-on-capped,
ack-on-already-applied, and the per-run bound."""

from __future__ import annotations

import json
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from unittest.mock import patch

from structlog.testing import capture_logs

from blizzard.foundation.clock import FixedClock
from blizzard.runner.harness.adapter import WorkerHandle
from blizzard.runner.harness.transcript import NormalizedTurn, TranscriptBatch, TranscriptPosition
from blizzard.runner.loop import transcript_drain as transcript_drain_module
from blizzard.runner.loop.context import LoopConfig
from blizzard.runner.loop.transcript_drain import TranscriptDrain
from blizzard.runner.store.repository import NewLease
from blizzard.wire.transcript_segment import TranscriptSegmentBatch, TranscriptSegmentRecord
from tests.runner_fakes import (
    FakeHarness,
    FakeHub,
    FakeProbe,
    FakeProvider,
    FakeTranscriptSource,
    make_context,
    make_store,
)

_NOW = datetime(2026, 7, 13, 12, 0, 0, tzinfo=UTC)


@dataclass
class _SteppingClock(FixedClock):
    """Returns ``instant`` on the first call, ``instant`` pushed by ``step`` on every call
    after — simulates real wall-clock elapsing across one ``run()`` without monkeypatching a
    process-wide module: the drain's deadline flows through the injected clock, so a test
    substitutes by type instead of reaching into the stdlib ``time`` module."""

    step: timedelta = timedelta(seconds=0)
    calls: int = 0

    def now(self) -> datetime:
        self.calls += 1
        return self.instant if self.calls == 1 else self.instant + self.step


def _ctx(hub: FakeHub, *, clock: FixedClock | None = None):  # type: ignore[no-untyped-def]
    store = make_store("sqlite://")
    harness = FakeHarness(handle=WorkerHandle(session_id="sess-a", pid=1, process_start_time="1"), verdict=None)
    return make_context(
        store,
        hub=hub,
        provider=FakeProvider({"e1": "/ws/e1"}),
        harness=harness,
        probe=FakeProbe(),
        config=LoopConfig(runner_id="r1", workspace_id="ws1", transcripts_ship=False),
        clock=clock,
    )


def _spawn_one_segment(ctx) -> str:  # type: ignore[no-untyped-def]
    ctx.store.record_binding(chunk_id="ch_1", environment_id="e1", workdir="/ws/e1", bound_at=_NOW)
    ctx.store.record_lease(
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
    ctx.store.record_spawn("lease_1", pid=1, process_start_time="1", session_id="sess-a", spawned_at=_NOW)
    return ctx.store.open_transcript_segments()[0].segment_id


def _enqueue_delta(ctx, segment_id: str, *, cursor: str) -> int:  # type: ignore[no-untyped-def]
    payload = json.dumps(
        {
            "segment_id": segment_id,
            "chunk_id": "ch_1",
            "node_id": "nd_build",
            "epoch": 1,
            "spawn_generation": 1,
            "turn_range_start": 0,
            "turn_range_end": 0,
            "final": False,
            "normalizer_version": "fake/1",
            "harness_version": None,
            "turns": [],
        }
    )
    (seq,) = ctx.store.record_transcript_deltas(
        segment_id=segment_id,
        chunk_id="ch_1",
        cursor=cursor,
        shipped_bytes=1,
        shipped_turns=1,
        normalizer_version="fake/1",
        harness_version=None,
        payloads=[payload],
        created_at=_NOW,
    )
    return seq


def test_drain_run_pumps_then_flushes_a_real_pump_output_to_the_hub_with_shipping_on() -> None:
    """review F9: no other case drives the real composition (pump then flush, ON) — a
    mutation probe confirmed a no-op pump left the whole suite green. Asserts the hub
    received the pump's own turns, not a hand-built stand-in."""
    hub = FakeHub()
    store = make_store("sqlite://")
    turn = NormalizedTurn(
        index=0,
        kind="asst",
        timestamp=_NOW,
        text="hello from the real pump",
        tool=None,
        thinking_redacted=False,
        sidechain=None,
        truncated=False,
    )
    source = FakeTranscriptSource(
        batches_by_session={
            "sess-a": TranscriptBatch(
                session_id="sess-a",
                available=True,
                reason=None,
                turns=[turn],
                unlinked_sidechains=[],
                next_position=TranscriptPosition("pos-1"),
                complete=True,
                truncated=False,
                sidechain_truncated=False,
                normalizer_version="fake/1",
                harness_version=None,
            )
        }
    )
    harness = FakeHarness(
        handle=WorkerHandle(session_id="sess-a", pid=1, process_start_time="1"),
        verdict=None,
        transcript_source=source,
    )
    ctx = make_context(
        store,
        hub=hub,
        provider=FakeProvider({"e1": "/ws/e1"}),
        harness=harness,
        probe=FakeProbe(),
        config=LoopConfig(runner_id="r1", workspace_id="ws1", transcripts_ship=True),
    )
    segment_id = _spawn_one_segment(ctx)

    TranscriptDrain(ctx).run()

    assert [r.segment_id for r in hub.transcripts_pushed] == [segment_id]
    pushed = hub.transcripts_pushed[0]
    assert pushed.final is False
    assert [t.text for t in pushed.turns] == ["hello from the real pump"]
    assert ctx.store.pending_transcript_outbound() == []
    segment = ctx.store.transcript_segment(segment_id)
    assert segment is not None
    assert segment.cursor == "pos-1"  # the pump's own cursor advance landed too


def test_drain_renders_a_final_marker_from_the_ledger_row_not_a_hand_built_payload() -> None:
    """review F8: the store buffers only a minimal marker row — the wire-shaped record
    renders here, straight from the segment's own frozen ledger row, not a second
    independently-built copy."""
    hub = FakeHub()
    ctx = _ctx(hub)
    segment_id = _spawn_one_segment(ctx)
    content_payload = json.dumps(
        {
            "segment_id": segment_id,
            "chunk_id": "ch_1",
            "node_id": "nd_build",
            "epoch": 1,
            "spawn_generation": 1,
            "turn_range_start": 0,
            "turn_range_end": 2,
            "final": False,
            "normalizer_version": "claude-code/1.2",
            "harness_version": "1.2.3",
            "turns": [],
        }
    )
    ctx.store.record_transcript_deltas(
        segment_id=segment_id,
        chunk_id="ch_1",
        cursor="tok-1",
        shipped_bytes=100,
        shipped_turns=3,
        normalizer_version="claude-code/1.2",
        harness_version="1.2.3",
        payloads=[content_payload],
        created_at=_NOW,
    )
    ctx.store.record_closure(
        lease_id="lease_1", chunk_id="ch_1", node_id="nd_build", reason="transitioned", closed_at=_NOW
    )

    TranscriptDrain(ctx).run()

    final_pushes = [r for r in hub.transcripts_pushed if r.final]
    assert len(final_pushes) == 1
    final = final_pushes[0]
    assert final.segment_id == segment_id
    assert final.chunk_id == "ch_1"
    assert final.node_id == "nd_build"
    assert (final.epoch, final.spawn_generation) == (1, 1)
    assert (final.turn_range_start, final.turn_range_end) == (3, 2)  # empty — claims no new turns
    assert (final.normalizer_version, final.harness_version) == ("claude-code/1.2", "1.2.3")
    assert final.turns == []
    assert final.record_truncated is False


def test_drain_renders_a_final_marker_as_truncated_when_the_segment_carries_a_reason() -> None:
    """A segment whose only hub row ends up being the final marker (e.g. a sibling segment
    already exhausted the chunk budget) must still report `record_truncated: true` — not
    the hardcoded `False` a genuinely clean segment gets."""
    hub = FakeHub()
    ctx = _ctx(hub)
    segment_id = _spawn_one_segment(ctx)
    ctx.store.stop_transcript_segment_shipping(segment_id, reason="chunk_budget_exceeded")
    ctx.store.record_closure(
        lease_id="lease_1", chunk_id="ch_1", node_id="nd_build", reason="transitioned", closed_at=_NOW
    )

    TranscriptDrain(ctx).run()

    final_pushes = [r for r in hub.transcripts_pushed if r.final]
    assert len(final_pushes) == 1
    assert final_pushes[0].record_truncated is True


def test_drain_renders_a_final_marker_as_truncated_from_a_record_truncation_alone() -> None:
    """The other half of `_final_record`'s disjunction, which fails independently: a segment
    that shipped fine but had a record truncated (cap, unshippable, source-read, hub cap)
    latches `truncated_reason` WITHOUT ever latching `shipping_stopped_reason`."""
    hub = FakeHub()
    ctx = _ctx(hub)
    segment_id = _spawn_one_segment(ctx)
    ctx.store.mark_transcript_record_truncated(segment_id, reason="record_cap_exceeded", severity=1)
    ctx.store.record_closure(
        lease_id="lease_1", chunk_id="ch_1", node_id="nd_build", reason="transitioned", closed_at=_NOW
    )

    TranscriptDrain(ctx).run()

    segment = ctx.store.transcript_segment(segment_id)
    assert segment is not None
    assert segment.shipping_stopped_reason is None  # only the record-truncation half is set
    final_pushes = [r for r in hub.transcripts_pushed if r.final]
    assert len(final_pushes) == 1
    assert final_pushes[0].record_truncated is True


def test_drain_renders_a_final_marker_on_the_sentinel_version_when_no_pump_ever_ran() -> None:
    """review F8: a segment that closes with no pump read ever having run still carries its
    normalizer-version sentinel on the ledger row (``bzh``'s "never ran" convention) —
    the drain's rendering must surface exactly that, not a null or a crash."""
    hub = FakeHub()
    ctx = _ctx(hub)
    _spawn_one_segment(ctx)
    ctx.store.record_closure(
        lease_id="lease_1", chunk_id="ch_1", node_id="nd_build", reason="transitioned", closed_at=_NOW
    )

    TranscriptDrain(ctx).run()

    final_pushes = [r for r in hub.transcripts_pushed if r.final]
    assert len(final_pushes) == 1
    assert final_pushes[0].normalizer_version == ""  # the sentinel, never learned from a real read
    assert final_pushes[0].harness_version is None


def test_drain_delivers_pending_records_in_fifo_order_and_acks_them() -> None:
    hub = FakeHub()
    ctx = _ctx(hub)
    segment_id = _spawn_one_segment(ctx)
    _enqueue_delta(ctx, segment_id, cursor="pos-1")
    _enqueue_delta(ctx, segment_id, cursor="pos-2")

    TranscriptDrain(ctx).run()

    assert [r.segment_id for r in hub.transcripts_pushed] == [segment_id, segment_id]
    assert ctx.store.pending_transcript_outbound() == []


def test_drain_stops_on_a_transport_failure_and_retries_the_backlog_next_tick() -> None:
    hub = FakeHub()
    hub.down = True
    ctx = _ctx(hub)
    segment_id = _spawn_one_segment(ctx)
    _enqueue_delta(ctx, segment_id, cursor="pos-1")
    _enqueue_delta(ctx, segment_id, cursor="pos-2")

    TranscriptDrain(ctx).run()

    assert hub.transcripts_pushed == []  # never even reached the hub
    assert len(ctx.store.pending_transcript_outbound()) == 2  # nothing acked — stays buffered

    hub.down = False
    TranscriptDrain(ctx).run()
    assert len(hub.transcripts_pushed) == 2
    assert ctx.store.pending_transcript_outbound() == []


def test_drain_acks_a_hub_capped_record_rather_than_wedging_the_fifo() -> None:
    """review F8: a cap rejection (blizzard#247's oversized/over-budget/over-rate reject) is
    not idempotency — the drain must still ack it and move on, never retry it forever."""
    hub = FakeHub()
    ctx = _ctx(hub)
    segment_id = _spawn_one_segment(ctx)
    capped_seq = _enqueue_delta(ctx, segment_id, cursor="pos-1")
    ok_seq = _enqueue_delta(ctx, segment_id, cursor="pos-2")
    hub.reject_transcript_seqs = {capped_seq}

    TranscriptDrain(ctx).run()

    assert ctx.store.pending_transcript_outbound() == []  # both acked — rejection is not a wedge
    assert [f.seq for f in hub.transcripts_pushed] == [ok_seq]  # the rejected one never applied


def test_drain_surfaces_a_hub_cap_rejection_never_silently() -> None:
    """review F6: never silent — the segment's own field records it and the fact lane
    carries a warning event, the same two-channel pattern the pump's own paths use."""
    hub = FakeHub()
    ctx = _ctx(hub)
    segment_id = _spawn_one_segment(ctx)
    capped_seq = _enqueue_delta(ctx, segment_id, cursor="pos-1")
    hub.reject_transcript_seqs = {capped_seq}

    TranscriptDrain(ctx).run()

    segment = ctx.store.transcript_segment(segment_id)
    assert segment is not None
    assert segment.truncated_reason == "hub_capped"
    fact_events = ctx.store.pending_outbound()
    assert len(fact_events) == 1
    payload = json.loads(fact_events[0].payload)
    assert payload["kind"] == "transcript-truncated"
    assert payload["detail"] == {"segment_id": segment_id, "reason": "hub_capped"}


def test_drain_marks_a_hub_cap_rejection_on_replay_after_a_lost_ack() -> None:
    """A crash in the after-submit.before-ack window (`_CP_AFTER_SUBMIT`) must not read
    the retry's ack as ordinary idempotency — the hub's replay response must still
    report `capped`, so the segment still gets marked and warned, not silently skipped."""
    hub = FakeHub()
    ctx = _ctx(hub)
    segment_id = _spawn_one_segment(ctx)
    seq = _enqueue_delta(ctx, segment_id, cursor="pos-1")
    hub.reject_transcript_seqs = {seq}
    # The hub side of a first delivery attempt completing durably, with no local
    # post-ack work ever running — the exact window `_CP_AFTER_SUBMIT` names.
    delta = ctx.store.pending_transcript_outbound()[0]
    record = TranscriptSegmentRecord.model_validate({"seq": delta.seq, **json.loads(delta.payload)})
    hub.push_transcripts(TranscriptSegmentBatch(runner_id="r1", records=[record]))
    assert ctx.store.pending_transcript_outbound() == [delta]  # still buffered — no local ack ran
    segment = ctx.store.transcript_segment(segment_id)
    assert segment is not None
    assert segment.truncated_reason is None  # not marked yet — the crash pre-empted it

    TranscriptDrain(ctx).run()  # the retry

    assert ctx.store.pending_transcript_outbound() == []  # finally cleared locally
    segment = ctx.store.transcript_segment(segment_id)
    assert segment is not None
    assert segment.truncated_reason == "hub_capped"
    fact_events = ctx.store.pending_outbound()
    assert len(fact_events) == 1
    assert json.loads(fact_events[0].payload)["kind"] == "transcript-truncated"


def test_drain_acks_an_already_applied_record_without_redelivering() -> None:
    """A replayed batch past the hub's own high-water mark reports already_applied — the
    drain still clears it locally rather than treating it as pending forever."""
    hub = FakeHub()
    ctx = _ctx(hub)
    segment_id = _spawn_one_segment(ctx)
    seq = _enqueue_delta(ctx, segment_id, cursor="pos-1")
    hub.transcript_high_water["r1"] = seq  # the hub has already seen this seq

    TranscriptDrain(ctx).run()

    assert ctx.store.pending_transcript_outbound() == []
    assert hub.transcripts_pushed == []  # never re-applied


def test_drain_bounds_its_own_per_run_record_count() -> None:
    """review F4: an unbounded drain would clear an entire backlog in one tick. Enforced
    entirely by the query's own clause (review F17), no redundant loop-level guard."""
    hub = FakeHub()
    ctx = _ctx(hub)
    segment_id = _spawn_one_segment(ctx)
    total = transcript_drain_module._MAX_RECORDS_PER_RUN + 5
    for i in range(total):
        _enqueue_delta(ctx, segment_id, cursor=f"pos-{i}")

    TranscriptDrain(ctx).run()

    assert len(hub.transcripts_pushed) == transcript_drain_module._MAX_RECORDS_PER_RUN
    assert len(ctx.store.pending_transcript_outbound()) == 5  # the rest waits for the next tick


def test_drain_bounds_its_own_per_run_wall_clock() -> None:
    """A SLOW but healthy hub (not a `HubClientError`) must still yield to the next tick.
    The stepping clock reports the deadline already elapsed on its second call, so the
    first record is never even attempted."""
    hub = FakeHub()
    step = timedelta(seconds=transcript_drain_module._MAX_SECONDS_PER_RUN + 1)
    ctx = _ctx(hub, clock=_SteppingClock(_NOW, step=step))
    segment_id = _spawn_one_segment(ctx)
    _enqueue_delta(ctx, segment_id, cursor="pos-1")
    _enqueue_delta(ctx, segment_id, cursor="pos-2")

    TranscriptDrain(ctx).run()

    assert hub.transcripts_pushed == []  # the wall-clock bound was already past on the first check
    assert len(ctx.store.pending_transcript_outbound()) == 2


def test_drain_never_queries_pending_once_the_pump_alone_exhausts_the_deadline() -> None:
    """Even capped at its own share of the budget (F9), the pump can still exhaust the
    RUN's own deadline outright — the flush must check the deadline before its own (up
    to 50-row) query, not just inside its loop after already paying for it."""
    hub = FakeHub()
    step = timedelta(seconds=transcript_drain_module._MAX_SECONDS_PER_RUN + 1)
    ctx = _ctx(hub, clock=_SteppingClock(_NOW, step=step))
    segment_id = _spawn_one_segment(ctx)
    _enqueue_delta(ctx, segment_id, cursor="pos-1")

    calls = 0
    real_pending = ctx.store.pending_transcript_outbound

    def _counting_pending(*args, **kwargs):  # type: ignore[no-untyped-def]
        nonlocal calls
        calls += 1
        return real_pending(*args, **kwargs)

    ctx.store.pending_transcript_outbound = _counting_pending  # type: ignore[method-assign]

    TranscriptDrain(ctx).run()

    assert calls == 0  # the query itself is never reached
    assert hub.transcripts_pushed == []


def test_drain_caps_the_pumps_own_deadline_to_a_fraction_of_the_run_budget() -> None:
    """Handed the run's full deadline, a slow-but-not-wedged pump starves the flush every tick.
    Pins that the pump gets a strictly smaller deadline than the run's own."""
    hub = FakeHub()
    ctx = _ctx(hub, clock=FixedClock(instant=_NOW))
    captured: dict[str, object] = {}
    original_run = transcript_drain_module.TranscriptPump.run

    def _capturing_run(self, *, deadline=None):  # type: ignore[no-untyped-def]
        captured["deadline"] = deadline
        return original_run(self, deadline=deadline)

    with patch.object(transcript_drain_module.TranscriptPump, "run", _capturing_run):
        TranscriptDrain(ctx).run()

    full_deadline = _NOW + timedelta(seconds=transcript_drain_module._MAX_SECONDS_PER_RUN)
    expected_pump_deadline = _NOW + timedelta(
        seconds=transcript_drain_module._MAX_SECONDS_PER_RUN * transcript_drain_module._PUMP_BUDGET_FRACTION
    )
    pump_deadline = captured["deadline"]
    assert isinstance(pump_deadline, datetime)
    assert pump_deadline == expected_pump_deadline
    assert pump_deadline < full_deadline  # the pump never gets the WHOLE run budget


class _RaisingTranscriptSource:
    """review F4: a `turns_since` that always raises — the isolation case no scripted
    :class:`FakeTranscriptSource` batch can trigger."""

    def turns_since(self, session_id: str, *, spawn_cwd: str | None, since: TranscriptPosition | None):  # type: ignore[no-untyped-def]
        raise RuntimeError("transcript source unavailable (scripted)")

    def read_raw_lines(self, session_id: str, *, spawn_cwd: str | None) -> list[str]:
        return []

    def size_bytes(self, session_id: str, *, spawn_cwd: str | None) -> int | None:
        return None


def test_drain_run_survives_a_raising_pump_and_recovers_next_run() -> None:
    """`TranscriptDrain.run` is not the last step in `tick`, so an uncaught raise must not
    propagate past it. One segment's own `turns_since` raising is isolated at the pump's
    per-segment level, so the buffered flush proceeds in the SAME run."""
    hub = FakeHub()
    store = make_store("sqlite://")
    harness = FakeHarness(
        handle=WorkerHandle(session_id="sess-a", pid=1, process_start_time="1"),
        verdict=None,
        transcript_source=_RaisingTranscriptSource(),
    )
    ctx = make_context(
        store,
        hub=hub,
        provider=FakeProvider({"e1": "/ws/e1"}),
        harness=harness,
        probe=FakeProbe(),
        config=LoopConfig(runner_id="r1", workspace_id="ws1", transcripts_ship=True),
    )
    segment_id = _spawn_one_segment(ctx)
    # A buffered record with nothing to do with this tick's own (failing) pump read: the raise
    # used to propagate out of `run()` and abort `_run_unsafe` before it reached this flush.
    _enqueue_delta(ctx, segment_id, cursor="pos-1")

    with capture_logs() as logs:
        TranscriptDrain(ctx).run()  # must not raise

    pump_failures = [e for e in logs if "failed to pump segment" in e["event"]]
    assert len(pump_failures) == 1  # isolated at the pump's own per-segment level
    drain_failures = [e for e in logs if "transcript drain failed" in e["event"]]
    assert drain_failures == []  # never reaches the outer, whole-tick catch
    assert hub.transcripts_pushed != []  # the pre-existing buffered delta still flushed, same run
    assert ctx.store.pending_transcript_outbound() == []

    # The condition ending (a healthy tick) recovers on the very next run — nothing wedged.
    healthy_ctx = replace(ctx, transcripts=FakeTranscriptSource())
    TranscriptDrain(healthy_ctx).run()  # must not raise either


class _RaisingOpenSegmentsStore:
    """Wraps a real store, but makes `open_transcript_segments` raise — the failure
    OUTSIDE `TranscriptPump`'s own per-segment loop (review round 6 F2's second half),
    which no per-segment try/except can isolate."""

    def __init__(self, inner):  # type: ignore[no-untyped-def]
        self._inner = inner

    def open_transcript_segments(self):  # type: ignore[no-untyped-def]
        raise RuntimeError("store unavailable (scripted)")

    def __getattr__(self, name: str):  # type: ignore[no-untyped-def]
        return getattr(self._inner, name)


def test_drain_run_survives_the_pump_itself_raising_outside_the_segment_loop() -> None:
    """A failure before `TranscriptPump.run`'s segment loop starts is out of reach of the
    per-segment try/except, so `_run_unsafe` must wrap the pump call itself too."""
    hub = FakeHub()
    ctx = _ctx(hub)
    ctx = replace(ctx, config=replace(ctx.config, transcripts_ship=True))
    segment_id = _spawn_one_segment(ctx)
    _enqueue_delta(ctx, segment_id, cursor="pos-1")
    ctx = replace(ctx, store=_RaisingOpenSegmentsStore(ctx.store))

    with capture_logs() as logs:
        TranscriptDrain(ctx).run()  # must not raise

    pump_failures = [e for e in logs if "transcript pump failed" in e["event"]]
    assert len(pump_failures) == 1
    drain_failures = [e for e in logs if "transcript drain failed" in e["event"]]
    assert drain_failures == []  # caught at the pump-call site, not the outer whole-tick catch
    assert hub.transcripts_pushed != []  # the buffered flush still ran despite the pump's own raise

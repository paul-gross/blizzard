"""The transcript lane's drain (component tier, issue #246) — break-on-error, ack-on-rejected,
ack-on-already-applied, and the per-run bound."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from blizzard.foundation.clock import FixedClock
from blizzard.runner.harness.adapter import WorkerHandle
from blizzard.runner.loop import transcript_drain as transcript_drain_module
from blizzard.runner.loop.context import LoopConfig
from blizzard.runner.loop.transcript_drain import TranscriptDrain
from blizzard.runner.store.repository import NewLease
from tests.runner_fakes import FakeHarness, FakeHub, FakeProbe, FakeProvider, make_context, make_store

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
    return ctx.store.record_transcript_delta(
        segment_id=segment_id,
        chunk_id="ch_1",
        cursor=cursor,
        shipped_bytes=1,
        shipped_turns=1,
        payload="{}",
        created_at=_NOW,
    )


def test_drain_delivers_pending_records_in_fifo_order_and_acks_them() -> None:
    hub = FakeHub()
    ctx = _ctx(hub)
    segment_id = _spawn_one_segment(ctx)
    _enqueue_delta(ctx, segment_id, cursor="pos-1")
    _enqueue_delta(ctx, segment_id, cursor="pos-2")

    TranscriptDrain(ctx).run()

    assert [f.payload for f in hub.transcripts_pushed] == [{}, {}]
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


def test_drain_acks_a_hub_rejected_record_rather_than_wedging_the_fifo() -> None:
    """review F8: a contract rejection (unknown kind, over-cap record) is not idempotency —
    the drain must still ack it and move on, never retry it forever."""
    hub = FakeHub()
    ctx = _ctx(hub)
    segment_id = _spawn_one_segment(ctx)
    rejected_seq = _enqueue_delta(ctx, segment_id, cursor="pos-1")
    ok_seq = _enqueue_delta(ctx, segment_id, cursor="pos-2")
    hub.reject_transcript_seqs = {rejected_seq}

    TranscriptDrain(ctx).run()

    assert ctx.store.pending_transcript_outbound() == []  # both acked — rejection is not a wedge
    assert [f.seq for f in hub.transcripts_pushed] == [ok_seq]  # the rejected one never applied


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
    """review F4: an unbounded drain would clear an entire backlog inside one ``tick()``,
    delaying every step after it — including the next tick's work for unrelated chunks.
    Bounded per run, the rest waits for the next tick."""
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

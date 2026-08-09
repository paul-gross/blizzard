"""The transcript lane's pump (component tier, issue #246) — cursor advance, the 1 MB
per-record and 64 MB per-chunk caps (D4), and the ``ship`` off-switch (D5)."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from blizzard.runner.harness.adapter import WorkerHandle
from blizzard.runner.harness.transcript import NormalizedTurn, TranscriptBatch, TranscriptPosition
from blizzard.runner.loop.context import LoopConfig
from blizzard.runner.loop.transcript_pump import CHUNK_TRANSCRIPT_MAX_BYTES, TranscriptPump
from blizzard.runner.store.repository import NewLease
from blizzard.wire.transcript_outbound import TRANSCRIPT_RECORD_MAX_BYTES
from tests.runner_fakes import (
    FakeHarness,
    FakeHub,
    FakeProbe,
    FakeProvider,
    FakeTranscriptSource,
    make_context,
    make_store,
)

pytestmark = pytest.mark.component

_NOW = datetime(2026, 7, 13, 12, 0, 0, tzinfo=UTC)


def _turn(index: int, text: str) -> NormalizedTurn:
    return NormalizedTurn(
        index=index,
        kind="asst",
        timestamp=_NOW,
        text=text,
        tool=None,
        thinking_redacted=False,
        sidechain=None,
        truncated=False,
    )


def _batch(turns: list[NormalizedTurn], *, next_token: str) -> TranscriptBatch:
    return TranscriptBatch(
        session_id="sess-a",
        available=True,
        reason=None,
        turns=turns,
        unlinked_sidechains=[],
        next_position=TranscriptPosition(next_token),
        complete=True,
        truncated=False,
        sidechain_truncated=False,
        normalizer_version="fake/1",
        harness_version=None,
    )


def _ctx(*, ship: bool, batches: dict[str, TranscriptBatch] | None = None):  # type: ignore[no-untyped-def]
    store = make_store("sqlite://")
    source = FakeTranscriptSource(batches_by_session=batches or {})
    harness = FakeHarness(
        handle=WorkerHandle(session_id="sess-a", pid=1, process_start_time="1"),
        verdict=None,
        transcript_source=source,
    )
    ctx = make_context(
        store,
        hub=FakeHub(),
        provider=FakeProvider({"e1": "/ws/e1"}),
        harness=harness,
        probe=FakeProbe(),
        config=LoopConfig(runner_id="r1", workspace_id="ws1", transcripts_ship=ship),
    )
    return ctx, source


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


def test_pump_is_a_noop_when_ship_is_false() -> None:
    ctx, source = _ctx(ship=False, batches={"sess-a": _batch([_turn(0, "hi")], next_token="pos-1")})
    _spawn_one_segment(ctx)
    TranscriptPump(ctx).run()
    assert ctx.store.pending_transcript_outbound() == []
    assert source.turns_since_calls == []  # the whole lane costs nothing when shipping is off


def test_pump_ships_a_delta_and_advances_the_cursor() -> None:
    ctx, _source = _ctx(ship=True, batches={"sess-a": _batch([_turn(0, "hi"), _turn(1, "there")], next_token="pos-1")})
    segment_id = _spawn_one_segment(ctx)

    TranscriptPump(ctx).run()

    pending = ctx.store.pending_transcript_outbound()
    assert len(pending) == 1
    assert pending[0].kind == "transcript.delta"
    assert pending[0].segment_id == segment_id
    segment = ctx.store.transcript_segment(segment_id)
    assert segment is not None
    assert (segment.cursor, segment.shipped_turns) == ("pos-1", 2)
    assert segment.shipped_bytes == len(pending[0].payload.encode("utf-8"))


def test_pump_never_reads_before_the_cursor() -> None:
    ctx, source = _ctx(ship=True, batches={"sess-a": _batch([_turn(0, "hi")], next_token="pos-1")})
    _spawn_one_segment(ctx)

    TranscriptPump(ctx).run()
    TranscriptPump(ctx).run()

    assert source.turns_since_calls[0][2] is None  # first read: from the start
    assert source.turns_since_calls[1][2] == TranscriptPosition("pos-1")  # second: carried forward


def test_pump_truncates_a_single_record_that_alone_exceeds_the_cap() -> None:
    """D4: a single turn over the 1 MB cap is truncated in place, not dropped — the
    delta still ships and the cursor still advances past it."""
    huge = "x" * (TRANSCRIPT_RECORD_MAX_BYTES + 1000)
    ctx, _source = _ctx(ship=True, batches={"sess-a": _batch([_turn(0, huge)], next_token="pos-1")})
    segment_id = _spawn_one_segment(ctx)

    TranscriptPump(ctx).run()

    pending = ctx.store.pending_transcript_outbound()
    assert len(pending) == 1  # still shipped, just shrunk
    assert len(pending[0].payload.encode("utf-8")) <= TRANSCRIPT_RECORD_MAX_BYTES
    segment = ctx.store.transcript_segment(segment_id)
    assert segment is not None
    assert segment.cursor == "pos-1"  # the cursor advances past the whole batch regardless
    assert segment.truncated_reason == "record_cap_exceeded"
    # Truncation is never silent (D4): a warning rides the FACT lane, not the transcript one.
    fact_events = ctx.store.pending_outbound()
    assert len(fact_events) == 1
    assert fact_events[0].kind == "event.recorded"


def test_pump_stops_shipping_past_the_chunk_budget_and_a_later_closure_still_finalizes() -> None:
    ctx, source = _ctx(ship=True, batches={"sess-a": _batch([_turn(0, "hi")], next_token="pos-1")})
    segment_id = _spawn_one_segment(ctx)
    # Fake the chunk already at its 64 MB budget via a prior delta, cheaply — no real content.
    ctx.store.record_transcript_delta(
        segment_id=segment_id,
        chunk_id="ch_1",
        cursor=None,
        shipped_bytes=CHUNK_TRANSCRIPT_MAX_BYTES,
        shipped_turns=0,
        payload="{}",
        created_at=_NOW,
    )

    TranscriptPump(ctx).run()

    segment = ctx.store.transcript_segment(segment_id)
    assert segment is not None
    assert segment.truncated_reason == "chunk_budget_exceeded"
    assert source.turns_since_calls == []  # never even read — the budget check comes first

    ctx.store.record_closure(
        lease_id="lease_1", chunk_id="ch_1", node_id="nd_build", reason="transitioned", closed_at=_NOW
    )
    finalized = ctx.store.transcript_segment(segment_id)
    assert finalized is not None
    assert finalized.finalized_at == _NOW  # truncated does not mean unfinalized

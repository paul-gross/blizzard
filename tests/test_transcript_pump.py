"""The transcript lane's pump (component tier, issue #246) — cursor advance, the 1 MB
per-record and 64 MB per-chunk caps (D4), and the ``ship`` off-switch (D5)."""

from __future__ import annotations

import json
from datetime import UTC, datetime

import pytest

from blizzard.runner.harness.adapter import WorkerHandle
from blizzard.runner.harness.transcript import (
    NormalizedTurn,
    SidechainConversation,
    ToolCall,
    TranscriptBatch,
    TranscriptPosition,
)
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


def _tool_call(output: str) -> ToolCall:
    return ToolCall(
        name="Bash",
        input={},
        input_unparsed=None,
        input_shape="object",
        tool_use_id="tool_1",
        output=output,
        output_truncated=False,
    )


def _tool_turn(index: int, *, output: str) -> NormalizedTurn:
    """A turn whose oversized content lives in ``tool.output``, not its own ``text`` — the
    ordinary shape of a Claude Code transcript (review F2)."""
    return NormalizedTurn(
        index=index,
        kind="tool",
        timestamp=_NOW,
        text="",
        tool=_tool_call(output),
        thinking_redacted=False,
        sidechain=None,
        truncated=False,
    )


def _sidechain_turn(index: int, *, sidechain_text: str) -> NormalizedTurn:
    """A turn whose oversized content lives in a nested sidechain turn's ``text`` (review F2)."""
    return NormalizedTurn(
        index=index,
        kind="tool",
        timestamp=_NOW,
        text="",
        tool=_tool_call(""),
        thinking_redacted=False,
        sidechain=SidechainConversation(
            agent_id="sub_1", agent_type="general", link="linked", turns=[_turn(0, sidechain_text)]
        ),
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
    """D4: a single turn over the 1 MB cap is truncated in place, not dropped. review F1:
    the TRANSIENT reason — ``truncated_reason``, never ``shipping_stopped_reason``."""
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
    assert segment.shipping_stopped_reason is None  # never latches the pump's guard
    # Truncation is never silent (D4): a warning rides the FACT lane, not the transcript one.
    fact_events = ctx.store.pending_outbound()
    assert len(fact_events) == 1
    assert fact_events[0].kind == "event.recorded"


def test_pump_keeps_shipping_after_a_record_cap_truncation() -> None:
    """review F1's exact regression: before the fix, a single oversized turn latched the
    SAME field the chunk-budget stop reads, permanently ending the segment. It must not —
    the very next tick, with a fresh small turn available, still ships."""
    huge = "x" * (TRANSCRIPT_RECORD_MAX_BYTES + 1000)
    ctx, source = _ctx(ship=True, batches={"sess-a": _batch([_turn(0, huge)], next_token="pos-1")})
    segment_id = _spawn_one_segment(ctx)

    TranscriptPump(ctx).run()
    assert ctx.store.transcript_segment(segment_id).truncated_reason == "record_cap_exceeded"  # type: ignore[union-attr]

    source._batches["sess-a"] = _batch([_turn(0, "small")], next_token="pos-2")
    TranscriptPump(ctx).run()

    assert source.turns_since_calls[1][2] == TranscriptPosition("pos-1")  # read past the truncated batch
    pending = ctx.store.pending_transcript_outbound()
    assert len(pending) == 2  # both ticks shipped — the record-cap event never stopped the second


def test_pump_shrinks_tool_output_not_just_top_level_text() -> None:
    """review F2: an oversized ``tool.output`` — the ordinary case for a Claude Code
    transcript, not an oversized ``text`` — must shrink too, or the record still ships
    over cap and the hub rejects it."""
    huge_output = "y" * (TRANSCRIPT_RECORD_MAX_BYTES + 1000)
    ctx, _source = _ctx(ship=True, batches={"sess-a": _batch([_tool_turn(0, output=huge_output)], next_token="pos-1")})
    segment_id = _spawn_one_segment(ctx)

    TranscriptPump(ctx).run()

    pending = ctx.store.pending_transcript_outbound()
    assert len(pending) == 1
    assert len(pending[0].payload.encode("utf-8")) <= TRANSCRIPT_RECORD_MAX_BYTES
    segment = ctx.store.transcript_segment(segment_id)
    assert segment is not None
    assert segment.truncated_reason == "record_cap_exceeded"
    assert segment.cursor == "pos-1"  # still shipped and advanced, not dropped


def test_pump_shrinks_a_nested_sidechain_turns_text() -> None:
    """review F2: an oversized turn nested under a sidechain conversation is exactly as
    shrinkable as a top-level one — the shrink must recurse into ``sidechain.turns``."""
    huge = "z" * (TRANSCRIPT_RECORD_MAX_BYTES + 1000)
    ctx, _source = _ctx(
        ship=True, batches={"sess-a": _batch([_sidechain_turn(0, sidechain_text=huge)], next_token="pos-1")}
    )
    segment_id = _spawn_one_segment(ctx)

    TranscriptPump(ctx).run()

    pending = ctx.store.pending_transcript_outbound()
    assert len(pending) == 1
    assert len(pending[0].payload.encode("utf-8")) <= TRANSCRIPT_RECORD_MAX_BYTES
    segment = ctx.store.transcript_segment(segment_id)
    assert segment is not None
    assert segment.truncated_reason == "record_cap_exceeded"
    assert segment.cursor == "pos-1"


def test_pump_ships_an_explicit_marker_when_shrinking_cannot_close_the_gap() -> None:
    """review F2: once every shrinkable field is empty, structural overhead alone can
    still exceed the cap — an explicit small-marker outcome, never a still-over-cap enqueue."""
    # Each turn's own JSON overhead (index/kind/timestamp/tool=None/…) is small but not
    # zero; enough turns with no shrinkable text still sums past the 1 MB cap.
    many_turns = [_turn(i, "") for i in range(20_000)]
    ctx, _source = _ctx(ship=True, batches={"sess-a": _batch(many_turns, next_token="pos-1")})
    segment_id = _spawn_one_segment(ctx)

    TranscriptPump(ctx).run()

    pending = ctx.store.pending_transcript_outbound()
    assert len(pending) == 1
    assert len(pending[0].payload.encode("utf-8")) <= TRANSCRIPT_RECORD_MAX_BYTES
    body = json.loads(pending[0].payload)
    assert body["turns"] == []
    assert body["turns_dropped"] == len(many_turns)
    segment = ctx.store.transcript_segment(segment_id)
    assert segment is not None
    assert segment.cursor == "pos-1"  # still advances — never re-reads the batch
    assert segment.truncated_reason == "record_unshippable"
    assert segment.shipping_stopped_reason is None  # transient, not a stop-shipping latch


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
    assert segment.shipping_stopped_reason == "chunk_budget_exceeded"
    assert segment.truncated_reason is None  # the two reasons are independent fields (F1)
    assert source.turns_since_calls == []  # never even read — the budget check comes first

    ctx.store.record_closure(
        lease_id="lease_1", chunk_id="ch_1", node_id="nd_build", reason="transitioned", closed_at=_NOW
    )
    finalized = ctx.store.transcript_segment(segment_id)
    assert finalized is not None
    assert finalized.finalized_at == _NOW  # truncated does not mean unfinalized


def test_pump_never_double_ships_after_a_same_session_resume() -> None:
    """review F3's pump-level regression: a same-session resume must not leave two open
    segments reading it — the pump only ever finds ONE, picking up where it left off."""
    ctx, source = _ctx(ship=True, batches={"sess-a": _batch([_turn(0, "hi")], next_token="pos-1")})
    _spawn_one_segment(ctx)
    TranscriptPump(ctx).run()
    first_pending = len(ctx.store.pending_transcript_outbound())
    assert first_pending == 1

    # A resume under a NEW lease generation, same session — record_spawn closes gen 1 out.
    ctx.store.record_spawn("lease_1", pid=2, process_start_time="2", session_id="sess-a", spawned_at=_NOW)
    open_segments = ctx.store.open_transcript_segments()
    assert len(open_segments) == 1  # exactly one open segment ever reads "sess-a"
    assert open_segments[0].cursor == "pos-1"  # carried forward, not re-read from the start

    source._batches["sess-a"] = _batch([_turn(0, "hi"), _turn(1, "more")], next_token="pos-2")
    TranscriptPump(ctx).run()

    assert source.turns_since_calls[-1][2] == TranscriptPosition("pos-1")  # reads from gen 1's cursor, not from None
    # gen 1's own final marker plus exactly one new delta on gen 2 — never a second delta
    # re-shipping "hi" from the start.
    pending = ctx.store.pending_transcript_outbound()
    assert len([d for d in pending if d.kind == "transcript.delta"]) == 2  # gen1's delta + gen2's one new delta
    assert len([d for d in pending if d.kind == "transcript.final"]) == 1  # gen1's own close-out

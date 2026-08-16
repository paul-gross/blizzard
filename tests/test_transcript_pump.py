"""The transcript lane's pump (component tier, issue #246) — cursor advance, the per-record
and 64 MB per-chunk caps (D4), the ``ship`` off-switch (D5), and blizzard#247's turn-range
wire shape."""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from structlog.testing import capture_logs

from blizzard.foundation.clock import FixedClock
from blizzard.hub.domain.transcripts import RECORD_MAX_BYTES as HUB_RECORD_MAX_BYTES
from blizzard.runner.harness.adapter import WorkerHandle
from blizzard.runner.harness.transcript import (
    LateToolOutput,
    NormalizedTurn,
    SidechainConversation,
    ToolCall,
    TranscriptBatch,
    TranscriptPosition,
)
from blizzard.runner.loop.attempt import FAILED, Attempt
from blizzard.runner.loop.context import LoopConfig
from blizzard.runner.loop.transcript_pump import (
    _ARRAY_SEPARATOR_BYTES,
    CHUNK_TRANSCRIPT_MAX_BYTES,
    MAX_BUFFERED_BYTES,
    PUMP_LEASE_MAX_SECONDS,
    TRANSCRIPT_RECORD_MAX_BYTES,
    TranscriptPump,
    _record_envelope,
    _record_overhead,
    _turn_wire,
)
from blizzard.runner.store.repository import BufferedTranscriptDelta, NewLease, TranscriptSegmentLedgerRow
from tests.runner_fakes import (
    FakeHarness,
    FakeHub,
    FakeProbe,
    FakeProvider,
    FakeTranscriptSource,
    StubbedBufferBytesStore,
    make_context,
    make_store,
)

pytestmark = pytest.mark.component

_NOW = datetime(2026, 7, 13, 12, 0, 0, tzinfo=UTC)


def _cap_share(fraction: float) -> int:
    """A byte count that is ``fraction`` of the live per-record cap. Every split/shrink test
    below sizes content through this, never in literal bytes: the cap has already moved once,
    and a batch written as "~1.5 MB, over cap" is simply under the new one — green, asserting
    nothing. The magnitude itself is pinned in `test_record_caps.py`, not here."""
    return max(1, int(TRANSCRIPT_RECORD_MAX_BYTES * fraction))


def _turns_over_cap(fraction: float) -> int:
    """How many no-text turns sum past ``fraction`` of the cap, MEASURED off the real wire
    shape. A hard-coded byte figure here drifted 3x unnoticed and silently tripled every
    batch derived from it — the count a test wants is a function of the shape, not a literal."""
    per_turn = len(json.dumps(_turn_wire(_turn(0, ""), 0)).encode("utf-8")) + _ARRAY_SEPARATOR_BYTES
    return _cap_share(fraction) // per_turn


def _empty_edits_over_cap(fraction: float) -> int:
    """The same, for `MultiEdit`-shaped `{"old_string": "", "new_string": ""}` entries."""
    per_edit = len(json.dumps({"old_string": "", "new_string": ""}).encode("utf-8")) + _ARRAY_SEPARATOR_BYTES
    return _cap_share(fraction) // per_edit


def _ledger_row_stub() -> TranscriptSegmentLedgerRow:
    """The ledger fields `_build_records` reads off a segment. Typed as the real row so a
    field added to that seam fails `blizzard:typecheck`, not at runtime inside a test."""
    return TranscriptSegmentLedgerRow(
        segment_id="seg_x",
        chunk_id="ch_1",
        node_id="nd_build",
        epoch=1,
        generation=1,
        lease_id="lease_1",
        session_id="sess-a",
        cursor=None,
        shipped_bytes=0,
        shipped_turns=0,
        normalizer_version="fake/1",
        harness_version="claude/9",
        truncated_reason=None,
        shipping_stopped_reason=None,
        supersedes=None,
        finalized_at=None,
        stamped_at=_NOW,
    )


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


def _tool_call(output: str, *, input_: dict[str, object] | None = None, input_unparsed: str | None = None) -> ToolCall:
    return ToolCall(
        name="Bash",
        input=input_ or {},
        input_unparsed=input_unparsed,
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


def _input_tool_turn(index: int, *, content: str) -> NormalizedTurn:
    """A ``Write``-shaped turn whose oversized content lives in ``tool.input["content"]``,
    never a turn's own ``text`` or a tool's ``output`` (F1)."""
    return NormalizedTurn(
        index=index,
        kind="tool",
        timestamp=_NOW,
        text="",
        tool=_tool_call("", input_={"content": content}),
        thinking_redacted=False,
        sidechain=None,
        truncated=False,
    )


def _unparsed_input_tool_turn(index: int, *, raw: str) -> NormalizedTurn:
    """The un-parsed half of the same shape: the harness handed over a tool-input blob the
    normalizer could not parse into a dict, so it survives only as ``input_unparsed``."""
    return NormalizedTurn(
        index=index,
        kind="tool",
        timestamp=_NOW,
        text="",
        tool=_tool_call("", input_unparsed=raw),
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


def _batch(
    turns: list[NormalizedTurn],
    *,
    next_token: str,
    unlinked_sidechains: list[SidechainConversation] | None = None,
    truncated: bool = False,
    sidechain_truncated: bool = False,
    late_tool_outputs: list[LateToolOutput] | None = None,
    agent_tool_use_ids: dict[str, str] | None = None,
) -> TranscriptBatch:
    return TranscriptBatch(
        session_id="sess-a",
        available=True,
        reason=None,
        turns=turns,
        unlinked_sidechains=unlinked_sidechains or [],
        next_position=TranscriptPosition(next_token),
        complete=True,
        truncated=truncated,
        sidechain_truncated=sidechain_truncated,
        normalizer_version="fake/1",
        harness_version=None,
        late_tool_outputs=late_tool_outputs or [],
        agent_tool_use_ids=agent_tool_use_ids or {},
    )


def _incomplete_batch(
    turns: list[NormalizedTurn], *, next_token: str, unlinked_sidechains: list[SidechainConversation] | None = None
) -> TranscriptBatch:
    """``_batch``'s ``complete=False`` counterpart — the per-batch read budget ran out
    before this whole window was covered, so ``next_position`` is where a caller resumes
    RIGHT NOW, not next tick (F2, review round 7)."""
    return replace(_batch(turns, next_token=next_token, unlinked_sidechains=unlinked_sidechains), complete=False)


def _assert_gapless_contiguous(pending: Sequence[BufferedTranscriptDelta], *, total_turns: int) -> list[dict[str, Any]]:
    """Every non-final buffered record's turn range, sorted, covers exactly
    ``[0, total_turns)`` with no gap and no overlap (F1, review round 7) — the invariant
    splitting an over-cap batch into several records must never break."""
    bodies: list[dict[str, Any]] = sorted((json.loads(d.payload) for d in pending), key=lambda b: b["turn_range_start"])
    expected_start = 0
    for body in bodies:
        assert body["turn_range_start"] == expected_start
        assert body["turn_range_end"] >= body["turn_range_start"]
        expected_start = body["turn_range_end"] + 1
    assert expected_start == total_turns
    return bodies


def _unlinked_sidechain(agent_id: str) -> SidechainConversation:
    return SidechainConversation(
        agent_id=agent_id, agent_type="general", link="unlinked", turns=[_turn(0, "orphaned subagent turn")]
    )


def _ctx(  # type: ignore[no-untyped-def]
    *,
    ship: bool,
    batches: dict[str, TranscriptBatch] | None = None,
    record_max_bytes: int | None = None,
    chunk_max_bytes: int | None = None,
):
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
        config=LoopConfig(
            runner_id="r1",
            workspace_id="ws1",
            transcripts_ship=ship,
            transcript_record_max_bytes=record_max_bytes,
            transcript_chunk_max_bytes=chunk_max_bytes,
        ),
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


def test_pump_skips_a_segment_already_stopped_from_shipping() -> None:
    """review F10: `_pump_one`'s first guard — an already-latched segment never reads the
    source at all. No prior test ever seeded `shipping_stopped_reason` first."""
    ctx, source = _ctx(ship=True, batches={"sess-a": _batch([_turn(0, "hi")], next_token="pos-1")})
    segment_id = _spawn_one_segment(ctx)
    ctx.store.stop_transcript_segment_shipping(segment_id, reason="chunk_budget_exceeded")

    TranscriptPump(ctx).run()

    assert source.turns_since_calls == []  # never even reached the source
    assert ctx.store.pending_transcript_outbound() == []


def test_pump_retries_from_the_same_cursor_when_the_source_is_unavailable() -> None:
    """review F10: `turns_since` can report `available=False` (e.g. the harness session
    file is mid-rotation) — the pump must leave the cursor untouched and ship nothing,
    retrying from the same position next tick. No prior test scripted this outcome."""
    unavailable = TranscriptBatch(
        session_id="sess-a",
        available=False,
        reason="not_found",
        turns=[],
        unlinked_sidechains=[],
        next_position=None,
        complete=True,
        truncated=False,
        sidechain_truncated=False,
        normalizer_version="fake/1",
        harness_version=None,
    )
    ctx, _source = _ctx(ship=True, batches={"sess-a": unavailable})
    segment_id = _spawn_one_segment(ctx)

    TranscriptPump(ctx).run()

    assert ctx.store.pending_transcript_outbound() == []
    segment = ctx.store.transcript_segment(segment_id)
    assert segment is not None
    assert segment.cursor is None  # untouched — nothing to advance to


def test_pump_ships_a_record_and_advances_the_cursor() -> None:
    ctx, _source = _ctx(ship=True, batches={"sess-a": _batch([_turn(0, "hi"), _turn(1, "there")], next_token="pos-1")})
    segment_id = _spawn_one_segment(ctx)

    TranscriptPump(ctx).run()

    pending = ctx.store.pending_transcript_outbound()
    assert len(pending) == 1
    assert pending[0].final is False
    assert pending[0].segment_id == segment_id
    body = json.loads(pending[0].payload)
    assert (body["turn_range_start"], body["turn_range_end"]) == (0, 1)  # blizzard#247's turn-range key
    assert body["normalizer_version"] == "fake/1"
    segment = ctx.store.transcript_segment(segment_id)
    assert segment is not None
    assert (segment.cursor, segment.shipped_turns) == ("pos-1", 2)
    assert segment.normalizer_version == "fake/1"
    assert segment.shipped_bytes == len(pending[0].payload.encode("utf-8"))


def test_pump_never_reads_before_the_cursor() -> None:
    ctx, source = _ctx(ship=True, batches={"sess-a": _batch([_turn(0, "hi")], next_token="pos-1")})
    _spawn_one_segment(ctx)

    TranscriptPump(ctx).run()
    # A real source never re-serves the same turns at the same position; scripting the
    # second read to advance keeps the fake honest about what `since` is carried into.
    source._batches["sess-a"] = _batch([_turn(1, "more")], next_token="pos-2")
    TranscriptPump(ctx).run()

    assert source.turns_since_calls[0][2] is None  # first read: from the start
    assert source.turns_since_calls[1][2] == TranscriptPosition("pos-1")  # second: carried forward


def test_pump_advances_the_cursor_on_a_turnless_batch() -> None:
    """A window can move the source's read position without producing any turn — e.g. a run
    of control records the normalizer drops. The cursor must still advance, or every later
    tick re-reads and re-normalizes the exact same bytes from the same stale position."""
    ctx, source = _ctx(ship=True, batches={"sess-a": _batch([], next_token="pos-1")})
    segment_id = _spawn_one_segment(ctx)

    TranscriptPump(ctx).run()

    segment = ctx.store.transcript_segment(segment_id)
    assert segment is not None
    assert segment.cursor == "pos-1"  # advanced despite shipping nothing
    assert segment.normalizer_version == "fake/1"  # learned even with nothing to ship
    assert ctx.store.pending_transcript_outbound() == []  # nothing to ship — no record enqueued

    source._batches["sess-a"] = _batch([_turn(0, "finally a turn")], next_token="pos-2")
    TranscriptPump(ctx).run()

    assert source.turns_since_calls[-1][2] == TranscriptPosition("pos-1")  # read from the advanced cursor
    pending = ctx.store.pending_transcript_outbound()
    assert len(pending) == 1
    assert ctx.store.transcript_segment(segment_id).cursor == "pos-2"  # type: ignore[union-attr]


def test_pump_truncates_a_single_record_that_alone_exceeds_the_cap() -> None:
    """D4: a single turn over the cap is truncated in place, not dropped. review F1:
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
    # Truncation is never silent (D4): a warning rides the FACT lane. review F12: assert
    # the actual payload, not just the generic envelope kind every fact-lane event shares.
    fact_events = ctx.store.pending_outbound()
    assert len(fact_events) == 1
    assert fact_events[0].kind == "event.recorded"
    warning = json.loads(fact_events[0].payload)
    assert warning["severity"] == "warning"
    assert warning["kind"] == "transcript-truncated"
    assert warning["detail"] == {"segment_id": segment_id, "reason": "record_cap_exceeded"}


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


def test_pump_warns_once_per_segment_when_every_tick_needs_truncation() -> None:
    """A segment hitting ``record_cap_exceeded`` every tick (never latched) must still warn
    only once — not once per tick, or a chatty session floods the fact lane."""
    huge = "x" * (TRANSCRIPT_RECORD_MAX_BYTES + 1000)
    ctx, source = _ctx(ship=True, batches={"sess-a": _batch([_turn(0, huge)], next_token="pos-1")})
    _spawn_one_segment(ctx)

    TranscriptPump(ctx).run()
    source._batches["sess-a"] = _batch([_turn(0, huge)], next_token="pos-2")
    TranscriptPump(ctx).run()
    source._batches["sess-a"] = _batch([_turn(0, huge)], next_token="pos-3")
    TranscriptPump(ctx).run()

    assert len(ctx.store.pending_transcript_outbound()) == 3  # every tick still shipped, shrunk
    fact_events = ctx.store.pending_outbound()
    assert len(fact_events) == 1  # exactly one warning across all three truncated ticks


def test_pump_warns_once_per_reason_even_as_the_segments_displayed_reason_alternates() -> None:
    """A guard on the DISPLAYED reason changing re-warns on every tick for a segment whose
    reason alternates. The warning latches per (segment, reason) instead."""
    ctx, source = _ctx(ship=True, batches={"sess-a": _batch([_turn(0, "hi")], next_token="pos-1", truncated=True)})
    segment_id = _spawn_one_segment(ctx)

    TranscriptPump(ctx).run()  # tick 1: source_read_truncated (mild) — new reason, warns

    huge = "x" * (TRANSCRIPT_RECORD_MAX_BYTES + 1000)
    source._batches["sess-a"] = _batch([_turn(0, huge)], next_token="pos-2")
    TranscriptPump(ctx).run()  # tick 2: record_cap_exceeded (worse) — new reason, warns

    source._batches["sess-a"] = _batch([_turn(0, "bye")], next_token="pos-3", truncated=True)
    TranscriptPump(ctx).run()  # tick 3: source_read_truncated AGAIN — same reason, no re-warn

    fact_events = ctx.store.pending_outbound()
    kinds = [json.loads(e.payload)["kind"] for e in fact_events]
    assert kinds.count("transcript-truncated") == 2  # exactly one per DISTINCT reason, not per tick
    segment = ctx.store.transcript_segment(segment_id)
    assert segment is not None
    # The milder reason reappearing on tick 3 never overwrites the worse one still standing
    # (explicit severity, not last-write-wins).
    assert segment.truncated_reason == "record_cap_exceeded"


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
    body = json.loads(pending[0].payload)
    assert body["turns"] != []  # shrunk, not emptied — genuine content still shipped
    # review F6: mildly over cap shrinks by a sliver, not to near-nothing.
    assert len(body["turns"][0]["tool"]["output"]) > len(huge_output) * 0.8
    # review F7: shrinking alone (not just the still-over-cap empty-slice case) is a real
    # loss too — the wire flag must say so, not just the local variable that drove it.
    assert body["record_truncated"] is True
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
    body = json.loads(pending[0].payload)
    # review F6: mildly over cap shrinks by a sliver, not to near-nothing.
    assert len(body["turns"][0]["sidechain"]["turns"][0]["text"]) > len(huge) * 0.8
    segment = ctx.store.transcript_segment(segment_id)
    assert segment is not None
    assert segment.truncated_reason == "record_cap_exceeded"
    assert segment.cursor == "pos-1"


def test_record_overhead_bounds_every_group_envelope_the_batch_can_produce() -> None:
    """One overhead measurement budgets every group, so it must be an UPPER bound on all of
    them — over BOTH range fields, since a later group's start widens just as its end does,
    and a bound over one axis alone leaves the other's digits under-counted."""
    segment = _ledger_row_stub()
    batch = _batch([_turn(0, "")], next_token="pos-1")
    turn_count = 100_000

    budgeted = _record_overhead(segment, batch, turn_range_start=0, turn_count=turn_count)

    # Every (start, end) a group of this batch can close on, both fields varied independently.
    for start in (0, 9, 99, 9_999, turn_count):
        for end in (start, start + 9, turn_count):
            envelope = _record_envelope(segment, batch, turn_range_start=start, turn_range_end=end)
            assert len(json.dumps(envelope).encode("utf-8")) <= budgeted, f"under-counted at {start}..{end}"


def test_pump_splits_many_small_turns_instead_of_emptying_the_whole_batch() -> None:
    """review round 7 F1: before the fix, a WHOLE batch whose structural overhead alone
    exceeded the cap got emptied in one explicit-empty record. Splitting into several
    under-cap records instead needs no shrinking and drops nothing."""
    # Each turn's own JSON overhead (index/kind/timestamp/tool=None/…) is small but not
    # zero; enough turns with no shrinkable text still sums past the cap.
    many_turns = [_turn(i, "") for i in range(_turns_over_cap(1.5))]
    ctx, _source = _ctx(ship=True, batches={"sess-a": _batch(many_turns, next_token="pos-1")})
    segment_id = _spawn_one_segment(ctx)

    TranscriptPump(ctx).run()

    pending = ctx.store.pending_transcript_outbound()
    assert len(pending) > 1  # split, not emptied whole
    for delta in pending:
        assert len(delta.payload.encode("utf-8")) <= TRANSCRIPT_RECORD_MAX_BYTES
    bodies = _assert_gapless_contiguous(pending, total_turns=len(many_turns))
    assert all(body["turns"] != [] for body in bodies)  # nothing dropped
    assert all(body["record_truncated"] is False for body in bodies)
    segment = ctx.store.transcript_segment(segment_id)
    assert segment is not None
    assert segment.cursor == "pos-1"  # advances once — past the WHOLE batch
    assert segment.shipped_turns == len(many_turns)
    assert segment.truncated_reason is None  # splitting alone closed the gap
    assert segment.shipping_stopped_reason is None


def test_pump_splits_a_batch_with_many_large_shrinkable_fields_instead_of_shrinking_them() -> None:
    """A batch whose oversized content spans many turns — a real catch-up window's shape —
    now splits into several under-cap records with every byte intact (review round 7 F1),
    rather than shrinking one combined record's content down to fit."""
    many_turns = [_tool_turn(i, output="x" * _cap_share(0.03)) for i in range(60)]  # ~1.8 caps' worth
    ctx, _source = _ctx(ship=True, batches={"sess-a": _batch(many_turns, next_token="pos-1")})
    segment_id = _spawn_one_segment(ctx)

    TranscriptPump(ctx).run()

    pending = ctx.store.pending_transcript_outbound()
    assert len(pending) > 1  # split, not one shrunk record
    for delta in pending:
        assert len(delta.payload.encode("utf-8")) <= TRANSCRIPT_RECORD_MAX_BYTES
    bodies = _assert_gapless_contiguous(pending, total_turns=len(many_turns))
    outputs = [t["tool"]["output"] for body in bodies for t in body["turns"]]
    assert len(outputs) == len(many_turns)  # every turn survives, not just some
    assert all(len(o) == _cap_share(0.03) for o in outputs)  # every byte survives — nothing shrunk
    segment = ctx.store.transcript_segment(segment_id)
    assert segment is not None
    assert segment.truncated_reason is None  # splitting alone closed the gap
    assert segment.cursor == "pos-1"


def test_pump_splits_a_severely_oversized_batch_instead_of_shrinking_every_field() -> None:
    """review round 7 F1: a window many times over cap — a real catch-up read's ordinary
    shape — splits into several fully-intact records. Mirrors the finding's own 50-turn
    measurement, which used to require lossy shrinking; splitting needs none."""
    window_turns = [_tool_turn(i, output="x" * _cap_share(0.06)) for i in range(50)]  # ~3 caps' worth
    ctx, _source = _ctx(ship=True, batches={"sess-a": _batch(window_turns, next_token="pos-1")})
    segment_id = _spawn_one_segment(ctx)

    TranscriptPump(ctx).run()

    pending = ctx.store.pending_transcript_outbound()
    assert len(pending) > 1  # split across several records
    for delta in pending:
        assert len(delta.payload.encode("utf-8")) <= TRANSCRIPT_RECORD_MAX_BYTES
    bodies = _assert_gapless_contiguous(pending, total_turns=len(window_turns))
    outputs = [t["tool"]["output"] for body in bodies for t in body["turns"]]
    assert len(outputs) == len(window_turns)  # never drops a turn
    assert all(len(o) == _cap_share(0.06) for o in outputs)  # never shrinks one either
    segment = ctx.store.transcript_segment(segment_id)
    assert segment is not None
    assert segment.truncated_reason is None
    assert segment.cursor == "pos-1"


def test_pump_shrinks_an_oversized_tool_input_value_instead_of_emptying_the_record() -> None:
    """A `Write`-shaped tool call's oversized `input["content"]` must be as shrinkable as
    a turn's own text or a tool's output — not silently left out, forcing the record to an
    empty-turns slice with the hub-accepted content lost entirely."""
    huge_content = "w" * (TRANSCRIPT_RECORD_MAX_BYTES + 1000)
    ctx, _source = _ctx(
        ship=True, batches={"sess-a": _batch([_input_tool_turn(0, content=huge_content)], next_token="pos-1")}
    )
    segment_id = _spawn_one_segment(ctx)

    TranscriptPump(ctx).run()

    pending = ctx.store.pending_transcript_outbound()
    assert len(pending) == 1
    assert len(pending[0].payload.encode("utf-8")) <= TRANSCRIPT_RECORD_MAX_BYTES
    body = json.loads(pending[0].payload)
    assert body["turns"] != []  # shrunk, not emptied — genuine content still shipped
    retained = body["turns"][0]["tool"]["input"]["content"]
    assert len(retained) > len(huge_content) * 0.8  # mildly over cap shrinks by a sliver
    assert body["turns"][0]["tool"]["input_truncated"] is True
    assert body["record_truncated"] is True
    segment = ctx.store.transcript_segment(segment_id)
    assert segment is not None
    assert segment.truncated_reason == "record_cap_exceeded"
    assert segment.cursor == "pos-1"


def test_pump_shrinks_non_ascii_content_by_a_real_fraction_not_to_near_zero() -> None:
    """Summing ``shrinkable`` in raw ``len()`` against a budget in escaped bytes clamps
    ``keep_fraction`` to 0.0 on CJK content, emptying a field only ~18% over cap."""
    huge_content = "文" * (_cap_share(1.18) // 6)  # each `文` escapes to 6 bytes; ~18% over cap
    ctx, _source = _ctx(
        ship=True, batches={"sess-a": _batch([_input_tool_turn(0, content=huge_content)], next_token="pos-1")}
    )
    segment_id = _spawn_one_segment(ctx)

    TranscriptPump(ctx).run()

    pending = ctx.store.pending_transcript_outbound()
    assert len(pending) == 1
    assert len(pending[0].payload.encode("utf-8")) <= TRANSCRIPT_RECORD_MAX_BYTES
    body = json.loads(pending[0].payload)
    assert body["turns"] != []  # shrunk, not emptied to near-zero on the first pass
    retained = body["turns"][0]["tool"]["input"]["content"]
    assert len(retained) > len(huge_content) * 0.5  # a real fraction survives, not a sliver
    assert body["turns"][0]["tool"]["input_truncated"] is True
    assert body["record_truncated"] is True
    segment = ctx.store.transcript_segment(segment_id)
    assert segment is not None
    assert segment.truncated_reason == "record_cap_exceeded"
    assert segment.cursor == "pos-1"


def test_pump_shrinks_an_oversized_unparsed_tool_input_instead_of_emptying_the_record() -> None:
    """`input_unparsed` is the second half of F1's fix and fails independently of the parsed
    `input` walk: an unparseable oversized blob is just as unshrinkable-looking, and the
    whole claimed range is lost the same way if the candidate is never offered."""
    huge_raw = "u" * (TRANSCRIPT_RECORD_MAX_BYTES + 1000)
    ctx, _source = _ctx(
        ship=True, batches={"sess-a": _batch([_unparsed_input_tool_turn(0, raw=huge_raw)], next_token="pos-1")}
    )
    segment_id = _spawn_one_segment(ctx)

    TranscriptPump(ctx).run()

    pending = ctx.store.pending_transcript_outbound()
    assert len(pending) == 1
    assert len(pending[0].payload.encode("utf-8")) <= TRANSCRIPT_RECORD_MAX_BYTES
    body = json.loads(pending[0].payload)
    assert body["turns"] != []  # shrunk, not emptied
    retained = body["turns"][0]["tool"]["input_unparsed"]
    assert 0 < len(retained) < len(huge_raw)
    assert body["turns"][0]["tool"]["input_truncated"] is True  # the shared tool-level marker
    assert body["record_truncated"] is True
    segment = ctx.store.transcript_segment(segment_id)
    assert segment is not None
    assert segment.truncated_reason == "record_cap_exceeded"


def test_pump_splits_many_medium_tool_inputs_instead_of_emptying_the_record() -> None:
    """Many ordinary `Edit`-shaped calls, each individually under cap, summing well over it
    — the batch splits into several under-cap records, each fully intact (review round 7
    F1), not a single shrunk-or-emptied one."""
    edits = [_input_tool_turn(i, content="e" * _cap_share(0.03)) for i in range(60)]  # ~1.8 caps' worth
    ctx, _source = _ctx(ship=True, batches={"sess-a": _batch(edits, next_token="pos-1")})
    segment_id = _spawn_one_segment(ctx)

    TranscriptPump(ctx).run()

    pending = ctx.store.pending_transcript_outbound()
    assert len(pending) > 1  # split, not one shrunk record
    for delta in pending:
        assert len(delta.payload.encode("utf-8")) <= TRANSCRIPT_RECORD_MAX_BYTES
    bodies = _assert_gapless_contiguous(pending, total_turns=len(edits))
    contents = [t["tool"]["input"]["content"] for body in bodies for t in body["turns"]]
    assert len(contents) == len(edits)  # every turn survives, not just some
    assert all(len(c) == _cap_share(0.03) for c in contents)  # nothing shrunk
    segment = ctx.store.transcript_segment(segment_id)
    assert segment is not None
    assert segment.truncated_reason is None  # splitting alone closed the gap
    assert segment.cursor == "pos-1"


def _assert_skipped_not_raised(logs: Sequence[Mapping[str, object]]) -> None:
    """Pins the cursor-advance guard's FORM, not just its existence: a bare `assert` (the
    pre-F3 code, and what `python -O` strips) is indistinguishable by outcome alone —
    `_pump_one_safe`'s own per-segment catch (F2) swallows the `AssertionError`, leaving
    the same empty buffer and unadvanced cursor. Only the log tells them apart: the skip
    line fired, the isolation's failure line did not."""
    assert [e for e in logs if "cursor did not advance" in str(e["event"])] != []
    assert [e for e in logs if "failed to pump segment" in str(e["event"])] == []


def test_pump_skips_rather_than_silently_re_shipping_when_turns_carry_no_next_position() -> None:
    """A batch with turns but no `next_position` is permitted by the source seam; enqueuing it
    re-ships the same turns forever. The guard skips and logs rather than asserting."""
    stuck = TranscriptBatch(
        session_id="sess-a",
        available=True,
        reason=None,
        turns=[_turn(0, "hi")],
        unlinked_sidechains=[],
        next_position=None,
        complete=True,
        truncated=False,
        sidechain_truncated=False,
        normalizer_version="fake/1",
        harness_version=None,
    )
    ctx, _source = _ctx(ship=True, batches={"sess-a": stuck})
    segment_id = _spawn_one_segment(ctx)

    with capture_logs() as logs:
        TranscriptPump(ctx).run()  # must return cleanly, not raise

    _assert_skipped_not_raised(logs)
    assert ctx.store.pending_transcript_outbound() == []  # never enqueued
    segment = ctx.store.transcript_segment(segment_id)
    assert segment is not None
    assert segment.cursor is None  # never advanced — the pump never had anywhere real to advance to
    assert segment.shipped_turns == 0


def test_pump_skips_when_an_already_pumped_segments_cursor_would_not_advance() -> None:
    """The same stuck source against a segment that HAS pumped: `new_cursor` falls back to its
    own cursor, so a null-check passes. The guard is that the cursor CHANGED, and it skips
    rather than raises, so the loop's other segments still proceed."""
    ctx, source = _ctx(ship=True, batches={"sess-a": _batch([_turn(0, "first")], next_token="pos-1")})
    segment_id = _spawn_one_segment(ctx)
    TranscriptPump(ctx).run()
    assert ctx.store.transcript_segment(segment_id).cursor == "pos-1"  # type: ignore[union-attr]

    source._batches["sess-a"] = TranscriptBatch(
        session_id="sess-a",
        available=True,
        reason=None,
        turns=[_turn(0, "second"), _turn(1, "third")],
        unlinked_sidechains=[],
        next_position=None,  # stuck: turns to ship, nowhere to advance to
        complete=True,
        truncated=False,
        sidechain_truncated=False,
        normalizer_version="fake/1",
        harness_version=None,
    )

    with capture_logs() as logs:
        TranscriptPump(ctx).run()  # must return cleanly, not raise

    _assert_skipped_not_raised(logs)
    assert len(ctx.store.pending_transcript_outbound()) == 1  # only the first pump's record
    segment = ctx.store.transcript_segment(segment_id)
    assert segment is not None
    assert segment.shipped_turns == 1  # the re-ship never happened


def test_pump_marks_and_warns_when_the_source_read_itself_came_back_truncated() -> None:
    """`TranscriptBatch.truncated` (the main file's own tail cap) must latch the segment,
    fire a fact-lane warning, and — since this batch carries turns — mark the shipped
    wire record `record_truncated: true` too, not just the local, silent flag."""
    batch = _batch([_turn(0, "hi")], next_token="pos-1", truncated=True)
    ctx, _source = _ctx(ship=True, batches={"sess-a": batch})
    segment_id = _spawn_one_segment(ctx)

    TranscriptPump(ctx).run()

    segment = ctx.store.transcript_segment(segment_id)
    assert segment is not None
    assert segment.truncated_reason == "source_read_truncated"
    fact_events = ctx.store.pending_outbound()
    kinds = [json.loads(e.payload)["kind"] for e in fact_events]
    assert "transcript-truncated" in kinds
    pending = ctx.store.pending_transcript_outbound()
    assert len(pending) == 1
    body = json.loads(pending[0].payload)
    assert body["record_truncated"] is True


def test_pump_marks_and_warns_when_the_sidechain_fanout_budget_ran_out() -> None:
    """`TranscriptBatch.sidechain_truncated` (the sidecar fan-out budget) is a distinct
    read-incompleteness signal from the main file's own tail cap, and must latch and warn
    exactly the same way."""
    batch = _batch([_turn(0, "hi")], next_token="pos-1", sidechain_truncated=True)
    ctx, _source = _ctx(ship=True, batches={"sess-a": batch})
    segment_id = _spawn_one_segment(ctx)

    TranscriptPump(ctx).run()

    segment = ctx.store.transcript_segment(segment_id)
    assert segment is not None
    assert segment.truncated_reason == "source_read_truncated"
    fact_events = ctx.store.pending_outbound()
    kinds = [json.loads(e.payload)["kind"] for e in fact_events]
    assert "transcript-truncated" in kinds


def test_pump_marks_and_warns_on_a_truncated_source_read_with_no_turns() -> None:
    """The turnless branch can be tail/sidecar-truncated too — no turns to claim a range
    over, but the loss still needs a trace, exactly like the with-turns case above."""
    batch = _batch([], next_token="pos-1", truncated=True)
    ctx, _source = _ctx(ship=True, batches={"sess-a": batch})
    segment_id = _spawn_one_segment(ctx)

    TranscriptPump(ctx).run()

    assert ctx.store.pending_transcript_outbound() == []  # no wire record — nothing to ship
    segment = ctx.store.transcript_segment(segment_id)
    assert segment is not None
    assert segment.truncated_reason == "source_read_truncated"
    fact_events = ctx.store.pending_outbound()
    kinds = [json.loads(e.payload)["kind"] for e in fact_events]
    assert "transcript-truncated" in kinds


def test_pump_stops_shipping_past_the_chunk_budget_and_a_later_closure_still_finalizes() -> None:
    ctx, source = _ctx(ship=True, batches={"sess-a": _batch([_turn(0, "hi")], next_token="pos-1")})
    segment_id = _spawn_one_segment(ctx)
    # Fake the chunk already at its 64 MB budget via a prior record, cheaply — no real content.
    ctx.store.record_transcript_deltas(
        segment_id=segment_id,
        chunk_id="ch_1",
        cursor=None,
        shipped_bytes=CHUNK_TRANSCRIPT_MAX_BYTES,
        shipped_turns=0,
        normalizer_version="fake/1",
        harness_version=None,
        payloads=["{}"],
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
    # A version WAS learned above (the seeded delta carried one), so closure still ships a
    # real final marker despite the budget stop.
    pending = ctx.store.pending_transcript_outbound()
    assert any(d.final for d in pending)


def test_a_raise_before_the_warning_leaves_the_dropped_sidechain_unlatched() -> None:
    """The latch is taken where the warning is emitted, not at the read: a failure between
    the two would otherwise mark the agent warned about while nothing ever warned."""
    batch = _batch([_turn(0, "hi")], next_token="pos-1", unlinked_sidechains=[_unlinked_sidechain("sub_1")])
    ctx, _source = _ctx(ship=True, batches={"sess-a": batch})
    _spawn_one_segment(ctx)
    real_record = ctx.store.record_transcript_deltas
    calls: list[int] = []

    def _raise_once(**kwargs: Any) -> None:
        calls.append(1)
        if len(calls) == 1:
            raise RuntimeError("enqueue failed")
        real_record(**kwargs)

    ctx.store.record_transcript_deltas = _raise_once  # type: ignore[method-assign]
    TranscriptPump(ctx).run()

    kinds = [json.loads(e.payload).get("kind") for e in ctx.store.pending_outbound()]
    assert "transcript-sidechain-dropped" not in kinds  # nothing warned on the failing tick

    TranscriptPump(ctx).run()  # the next tick re-reads the same batch and succeeds

    kinds = [json.loads(e.payload).get("kind") for e in ctx.store.pending_outbound()]
    assert kinds.count("transcript-sidechain-dropped") == 1


def test_pump_still_warns_a_dropped_sidechain_on_the_tick_that_tips_the_chunk_budget() -> None:
    """review F13: unlike the pre-read budget check above, THIS tick's own record (a
    real, just-read batch) is what tips the budget over — its dropped sidechain must
    still warn, not vanish silently along with the stop."""
    ctx, _source = _ctx(
        ship=True,
        batches={
            "sess-a": _batch([_turn(0, "hi")], next_token="pos-1", unlinked_sidechains=[_unlinked_sidechain("sub_1")])
        },
    )
    segment_id = _spawn_one_segment(ctx)
    # Close to the budget, not AT it — this tick's own record (read, not faked) is what tips it.
    ctx.store.record_transcript_deltas(
        segment_id=segment_id,
        chunk_id="ch_1",
        cursor=None,
        shipped_bytes=CHUNK_TRANSCRIPT_MAX_BYTES - 10,
        shipped_turns=0,
        normalizer_version="fake/1",
        harness_version=None,
        payloads=["{}"],
        created_at=_NOW,
    )

    TranscriptPump(ctx).run()

    segment = ctx.store.transcript_segment(segment_id)
    assert segment is not None
    assert segment.shipping_stopped_reason == "chunk_budget_exceeded"  # this tick's record tipped it
    fact_events = ctx.store.pending_outbound()
    kinds = [json.loads(e.payload)["kind"] for e in fact_events]
    assert "transcript-sidechain-dropped" in kinds  # never silently dropped alongside the stop


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
    # gen 1's own final marker plus exactly one new record on gen 2 — never a second record
    # re-shipping "hi" from the start.
    pending = ctx.store.pending_transcript_outbound()
    assert len([d for d in pending if not d.final]) == 2  # gen1's record + gen2's one new record
    assert len([d for d in pending if d.final]) == 1  # gen1's own close-out


def test_lease_close_pumps_the_open_segment_before_finalizing_it() -> None:
    """A lease closure finalizes its segment, dropping it out of every later tick's
    ``run()`` — so ``Attempt.close`` must pump the lease's own segment first, or content
    written since the last pump never ships."""
    ctx, _source = _ctx(
        ship=True, batches={"sess-a": _batch([_turn(0, "last output before failure")], next_token="pos-1")}
    )
    segment_id = _spawn_one_segment(ctx)
    lease = ctx.store.active_lease("lease_1")
    assert lease is not None

    Attempt(ctx, lease).close(FAILED, _NOW)

    segment = ctx.store.transcript_segment(segment_id)
    assert segment is not None
    assert segment.finalized_at is not None
    assert segment.cursor == "pos-1"  # the pre-closure content was actually read and shipped

    pending = ctx.store.pending_transcript_outbound()
    finals = [d.final for d in pending]
    assert False in finals  # the last output shipped...
    assert True in finals  # ...and closure's own marker followed
    assert finals.index(False) < finals.index(True)  # ...content before finalization


def test_pump_lease_yields_to_its_own_deadline() -> None:
    """review F4: ``pump_lease`` must honor an already-elapsed deadline exactly like
    ``run()`` does — the bound ``Attempt.close`` computes before calling it, so a slow
    transcript-source read can never delay the closure it precedes past a few seconds."""
    ctx, _source = _ctx(ship=True, batches={"sess-a": _batch([_turn(0, "hi")], next_token="pos-1")})
    _spawn_one_segment(ctx)
    lease = ctx.store.active_lease("lease_1")
    assert lease is not None

    TranscriptPump(ctx).pump_lease(lease.lease_id, deadline=ctx.clock.now())

    assert ctx.store.pending_transcript_outbound() == []  # bound already elapsed — nothing pumped


@dataclass
class _AdvancingClock(FixedClock):
    """Advances ``step`` on every read, so a deadline computed from one read is already past
    by the next — whatever the call order, without a fixture counting calls."""

    step: timedelta = timedelta(seconds=PUMP_LEASE_MAX_SECONDS * 2)
    calls: int = 0

    def now(self) -> datetime:
        self.calls += 1
        return self.instant + self.step * self.calls


def test_lease_close_bounds_the_pump_it_runs_before_closing() -> None:
    """review F4's other half: ``pump_lease`` honoring a deadline is worth nothing unless
    ``Attempt.close`` actually computes and passes one. Passing ``None`` there survives
    every other case, so the bound is pinned at the closure boundary itself."""
    ctx, source = _ctx(ship=True, batches={"sess-a": _batch([_turn(0, "hi")], next_token="pos-1")})
    ctx = replace(ctx, clock=_AdvancingClock(instant=_NOW))
    _spawn_one_segment(ctx)
    lease = ctx.store.active_lease("lease_1")
    assert lease is not None

    Attempt(ctx, lease).close(FAILED, _NOW)

    assert source.turns_since_calls == []  # the bound elapsed before the read, not during it
    pending = ctx.store.pending_transcript_outbound()
    assert [d.final for d in pending] == [True]  # only closure's own marker — the closure still landed


class _RaisingTranscriptSource:
    """An :class:`IHarnessTranscriptSource` whose ``turns_since`` always raises — review
    F4's exception-isolation case, which no fixture can script through
    :class:`FakeTranscriptSource` alone."""

    def turns_since(self, session_id: str, *, spawn_cwd: str | None, since: TranscriptPosition | None):  # type: ignore[no-untyped-def]
        raise RuntimeError("transcript source unavailable (scripted)")

    def read_raw_lines(self, session_id: str, *, spawn_cwd: str | None) -> list[str]:
        return []

    def size_bytes(self, session_id: str, *, spawn_cwd: str | None) -> int | None:
        return None

    def context_tokens(self, session_id: str, *, spawn_cwd: str | None) -> int | None:
        return None


def test_lease_close_survives_a_raising_transcript_source() -> None:
    """review F4: ``Attempt.close`` funnels every closure path through its own
    pre-closure pump call. A read that raises must not propagate past it — the
    closure, and whatever it accompanies, must still land."""
    store = make_store("sqlite://")
    harness = FakeHarness(
        handle=WorkerHandle(session_id="sess-a", pid=1, process_start_time="1"),
        verdict=None,
        transcript_source=_RaisingTranscriptSource(),
    )
    ctx = make_context(
        store,
        hub=FakeHub(),
        provider=FakeProvider({"e1": "/ws/e1"}),
        harness=harness,
        probe=FakeProbe(),
        config=LoopConfig(runner_id="r1", workspace_id="ws1", transcripts_ship=True),
    )
    segment_id = _spawn_one_segment(ctx)
    lease = ctx.store.active_lease("lease_1")
    assert lease is not None

    Attempt(ctx, lease).close(FAILED, _NOW)  # must not raise

    segment = ctx.store.transcript_segment(segment_id)
    assert segment is not None
    assert segment.finalized_at is not None  # record_closure ran regardless of the raise
    assert segment.cursor is None  # the pre-closure read never got a chance to advance it
    # verify round 8: surviving the raise isn't enough — through the real closure path
    # (not just a direct pump_lease call), the segment it finalized must carry a trace.
    assert segment.truncated_reason == "lease_closure_incomplete"


def test_run_yields_to_its_own_deadline_across_many_open_segments() -> None:
    """``run()`` iterates every open segment with no bound unless given a ``deadline``. One
    already past on entry must stop it before the first segment, leaving the rest for later."""
    ctx, _source = _ctx(ship=True, batches={"sess-a": _batch([_turn(0, "hi")], next_token="pos-1")})
    _spawn_one_segment(ctx)
    ctx.store.record_binding(chunk_id="ch_2", environment_id="e2", workdir="/ws/e2", bound_at=_NOW)
    ctx.store.record_lease(
        NewLease(
            lease_id="lease_2",
            chunk_id="ch_2",
            graph_id="gr_1",
            node_id="nd_build",
            node_name="build",
            epoch=1,
            runner_id="r1",
            retries_max=2,
            created_at=_NOW,
        )
    )
    ctx.store.record_spawn("lease_2", pid=2, process_start_time="2", session_id="sess-a", spawned_at=_NOW)

    TranscriptPump(ctx).run(deadline=_NOW)  # already past by the time the loop checks it

    assert ctx.store.pending_transcript_outbound() == []  # neither segment pumped this run
    assert all(s.cursor is None for s in ctx.store.open_transcript_segments())

    TranscriptPump(ctx).run()  # no deadline — catches both up on a later tick

    assert len(ctx.store.pending_transcript_outbound()) == 2


class _PartiallyRaisingTranscriptSource:
    """review round 6 F2: raises for one session, serves a real batch for another — the
    per-segment isolation case no single-session fixture can script."""

    def __init__(self, *, raising_session: str, batch: TranscriptBatch) -> None:
        self._raising_session = raising_session
        self._batch = batch

    def turns_since(self, session_id: str, *, spawn_cwd: str | None, since: TranscriptPosition | None):  # type: ignore[no-untyped-def]
        if session_id == self._raising_session:
            raise RuntimeError("transcript source unavailable (scripted)")
        return self._batch

    def read_raw_lines(self, session_id: str, *, spawn_cwd: str | None) -> list[str]:
        return []

    def size_bytes(self, session_id: str, *, spawn_cwd: str | None) -> int | None:
        return None

    def context_tokens(self, session_id: str, *, spawn_cwd: str | None) -> int | None:
        return None


def test_run_isolates_one_segments_pump_failure_from_the_rest() -> None:
    """Without a per-segment `try`, one segment's pump failure aborts `run()`'s whole loop
    before a later segment is attempted. One bad segment must not stop the others."""
    store = make_store("sqlite://")
    good_batch = _batch([_turn(0, "hi")], next_token="pos-1")
    source = _PartiallyRaisingTranscriptSource(raising_session="sess-bad", batch=good_batch)
    harness = FakeHarness(
        handle=WorkerHandle(session_id="sess-bad", pid=1, process_start_time="1"),
        verdict=None,
        transcript_source=source,
    )
    ctx = make_context(
        store,
        hub=FakeHub(),
        provider=FakeProvider({"e1": "/ws/e1", "e2": "/ws/e2"}),
        harness=harness,
        probe=FakeProbe(),
        config=LoopConfig(runner_id="r1", workspace_id="ws1", transcripts_ship=True),
    )
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
    ctx.store.record_spawn("lease_1", pid=1, process_start_time="1", session_id="sess-bad", spawned_at=_NOW)
    ctx.store.record_binding(chunk_id="ch_2", environment_id="e2", workdir="/ws/e2", bound_at=_NOW)
    ctx.store.record_lease(
        NewLease(
            lease_id="lease_2",
            chunk_id="ch_2",
            graph_id="gr_1",
            node_id="nd_build",
            node_name="build",
            epoch=1,
            runner_id="r1",
            retries_max=2,
            created_at=_NOW,
        )
    )
    ctx.store.record_spawn("lease_2", pid=2, process_start_time="2", session_id="sess-good", spawned_at=_NOW)

    TranscriptPump(ctx).run()  # must not raise despite "sess-bad"'s own failure

    pending = ctx.store.pending_transcript_outbound()
    assert len(pending) == 1  # the good segment's own record still shipped
    assert pending[0].chunk_id == "ch_2"
    bad_segment = next(s for s in ctx.store.open_transcript_segments() if s.chunk_id == "ch_1")
    assert bad_segment.cursor is None  # the failing segment's own read never advanced it


def test_pump_warns_on_an_unlinked_sidechain_dropped_alongside_a_normal_record() -> None:
    """A subagent conversation whose parent turn is outside the read window surfaces on
    ``batch.unlinked_sidechains``, never silently dropped. #247's schema has no field for
    it, so it rides its own fact-lane warning rather than the wire record."""
    batch = _batch([_turn(0, "hi")], next_token="pos-1", unlinked_sidechains=[_unlinked_sidechain("sub_1")])
    ctx, _source = _ctx(ship=True, batches={"sess-a": batch})
    _spawn_one_segment(ctx)

    TranscriptPump(ctx).run()

    pending = ctx.store.pending_transcript_outbound()
    assert len(pending) == 1
    body = json.loads(pending[0].payload)
    assert len(body["turns"]) == 1  # the ordinary turn ships normally
    fact_events = ctx.store.pending_outbound()
    assert len(fact_events) == 1
    warning = json.loads(fact_events[0].payload)
    assert warning["kind"] == "transcript-sidechain-dropped"
    assert warning["detail"]["agent_ids"] == ["sub_1"]


def test_pump_warns_on_an_unlinked_sidechain_dropped_with_no_turns() -> None:
    """The turnless-batch path must not silently advance the cursor when an unlinked
    sidechain was the only thing in the window — that loss still needs a trace, even
    though (unlike a truncation) it claims no turn range on the wire lane at all."""
    batch = _batch([], next_token="pos-1", unlinked_sidechains=[_unlinked_sidechain("sub_1")])
    ctx, _source = _ctx(ship=True, batches={"sess-a": batch})
    segment_id = _spawn_one_segment(ctx)

    TranscriptPump(ctx).run()

    assert ctx.store.pending_transcript_outbound() == []  # no wire record — nothing to claim a range over
    segment = ctx.store.transcript_segment(segment_id)
    assert segment is not None
    assert segment.cursor == "pos-1"  # still advances
    fact_events = ctx.store.pending_outbound()
    assert len(fact_events) == 1
    warning = json.loads(fact_events[0].payload)
    assert warning["kind"] == "transcript-sidechain-dropped"
    assert warning["detail"]["agent_ids"] == ["sub_1"]


def test_pump_warns_only_once_per_segment_per_agent_across_ticks() -> None:
    """review F2: an unlinked subagent stays unlinked every tick until it attaches or the
    segment closes — the fact-lane warning must latch per (segment, agent_id), not fire on
    every tick it recurs."""
    ctx, source = _ctx(
        ship=True,
        batches={
            "sess-a": _batch([_turn(0, "hi")], next_token="pos-1", unlinked_sidechains=[_unlinked_sidechain("sub_1")])
        },
    )
    _spawn_one_segment(ctx)

    TranscriptPump(ctx).run()
    source._batches["sess-a"] = _batch(
        [_turn(1, "again")], next_token="pos-2", unlinked_sidechains=[_unlinked_sidechain("sub_1")]
    )
    TranscriptPump(ctx).run()
    # A second, distinct subagent still gets its own first warning.
    source._batches["sess-a"] = _batch(
        [_turn(2, "again")],
        next_token="pos-3",
        unlinked_sidechains=[_unlinked_sidechain("sub_1"), _unlinked_sidechain("sub_2")],
    )
    TranscriptPump(ctx).run()

    assert len(ctx.store.pending_transcript_outbound()) == 3  # every tick still shipped its own record
    fact_events = ctx.store.pending_outbound()
    assert len(fact_events) == 2  # sub_1 once (tick 1), sub_2 once (tick 3) — never sub_1 again
    warnings = [json.loads(e.payload) for e in fact_events]
    assert [w["detail"]["agent_ids"] for w in warnings] == [["sub_1"], ["sub_2"]]


# --- review round 7 F1: split an over-cap batch into several records, not one --------


def test_pump_splits_a_batch_within_the_hub_cap_but_over_the_runner_cap() -> None:
    """review round 7 F1: a batch within the hub's own record cap but over the runner's
    smaller one splits into multiple records, each within cap, none emptied — the cursor
    advances exactly once, and the split records' ranges are contiguous, gapless."""
    # The midpoint of the two caps — in the band by construction, however either moves.
    per_turn = (TRANSCRIPT_RECORD_MAX_BYTES + HUB_RECORD_MAX_BYTES) // 2 // 5
    turns = [_tool_turn(i, output="x" * per_turn) for i in range(5)]
    ctx, _source = _ctx(ship=True, batches={"sess-a": _batch(turns, next_token="pos-1")})
    segment_id = _spawn_one_segment(ctx)

    TranscriptPump(ctx).run()

    pending = ctx.store.pending_transcript_outbound()
    assert len(pending) > 1  # split, not one shrunk/emptied record
    for delta in pending:
        assert len(delta.payload.encode("utf-8")) <= TRANSCRIPT_RECORD_MAX_BYTES
    bodies = _assert_gapless_contiguous(pending, total_turns=len(turns))
    assert all(body["turns"] != [] for body in bodies)  # none emptied
    outputs = [t["tool"]["output"] for body in bodies for t in body["turns"]]
    assert len(outputs) == len(turns)
    assert all(len(o) == per_turn for o in outputs)  # nothing shrunk either
    segment = ctx.store.transcript_segment(segment_id)
    assert segment is not None
    assert segment.cursor == "pos-1"  # advanced exactly once, past the WHOLE batch
    assert segment.shipped_turns == len(turns)
    assert segment.truncated_reason is None


def _unshrinkable_tool_turn(index: int) -> NormalizedTurn:
    """A `MultiEdit`-shaped turn with no shrinkable content at all (every string value is
    empty) whose sheer structural bulk alone still exceeds the cap, even shrunk to
    nothing — the single pathological turn F1(b) needs, isolated from any sibling."""
    edits = [{"old_string": "", "new_string": ""} for _ in range(_empty_edits_over_cap(1.5))]
    return NormalizedTurn(
        index=index,
        kind="tool",
        timestamp=_NOW,
        text="",
        tool=_tool_call("", input_={"edits": edits}),
        thinking_redacted=False,
        sidechain=None,
        truncated=False,
    )


def test_pump_isolates_a_single_pathological_turn_from_its_siblings() -> None:
    """review round 7 F1: a single turn over cap even after shrinking still falls back to
    an explicit empty-turns record, scoped to just its own range. Sibling turns in the
    same batch ship normally, in their own record(s), never swept up in its loss."""
    turns = [_turn(0, "before"), _unshrinkable_tool_turn(1), _turn(2, "after")]
    ctx, _source = _ctx(ship=True, batches={"sess-a": _batch(turns, next_token="pos-1")})
    segment_id = _spawn_one_segment(ctx)

    TranscriptPump(ctx).run()

    pending = ctx.store.pending_transcript_outbound()
    assert len(pending) == 3  # the pathological turn never merges with a sibling
    for delta in pending:
        assert len(delta.payload.encode("utf-8")) <= TRANSCRIPT_RECORD_MAX_BYTES
    bodies = _assert_gapless_contiguous(pending, total_turns=len(turns))
    empty_bodies = [b for b in bodies if b["turns"] == []]
    assert len(empty_bodies) == 1
    assert (empty_bodies[0]["turn_range_start"], empty_bodies[0]["turn_range_end"]) == (1, 1)  # scoped to just it
    assert empty_bodies[0]["record_truncated"] is True
    non_empty = [b for b in bodies if b["turns"] != []]
    shipped_texts = [t["text"] for body in non_empty for t in body["turns"]]
    assert shipped_texts == ["before", "after"]  # siblings ship normally, in full, unaffected
    segment = ctx.store.transcript_segment(segment_id)
    assert segment is not None
    assert segment.cursor == "pos-1"  # still advances past the whole batch
    assert segment.truncated_reason == "record_unshippable"


def test_pump_stops_shipping_a_split_batch_that_would_exceed_the_chunk_budget_when_summed() -> None:
    """review round 7 F1: because every record a split batch produces advances the SAME
    cursor write, they must ship all-or-nothing against the 64 MB per-chunk budget —
    summed across every record the batch would produce, not just checked against one."""
    turns = [_tool_turn(i, output="x" * 300_000) for i in range(5)]  # splits into >1 record
    ctx, _source = _ctx(ship=True, batches={"sess-a": _batch(turns, next_token="pos-1")})
    segment_id = _spawn_one_segment(ctx)
    # Close enough to the budget that THIS tick's own (summed, multi-record) total tips it,
    # but not already AT the budget — the pre-read guard already covers that simpler case.
    ctx.store.record_transcript_deltas(
        segment_id=segment_id,
        chunk_id="ch_1",
        cursor=None,
        shipped_bytes=CHUNK_TRANSCRIPT_MAX_BYTES - 1_000_000,
        shipped_turns=0,
        normalizer_version="fake/1",
        harness_version=None,
        payloads=["{}"],
        created_at=_NOW,
    )

    TranscriptPump(ctx).run()

    pending = ctx.store.pending_transcript_outbound()
    assert len(pending) == 1  # only the seeded delta above — none of THIS tick's records shipped
    segment = ctx.store.transcript_segment(segment_id)
    assert segment is not None
    assert segment.shipping_stopped_reason == "chunk_budget_exceeded"
    assert segment.cursor is None  # never advanced — nothing landed
    assert segment.shipped_turns == 0  # none of the split batch's turns were claimed


# --- review round 7 F2: `pump_lease` drains a segment fully, not just one window -----


class _SequencedTranscriptSource:
    """Serves a scripted sequence of batches for one session, one per call — the
    within-one-invocation multi-read case ``pump_lease``'s drain loop (F2) needs, which
    ``FakeTranscriptSource`` can't script (it needs external reassignment between calls,
    impossible from inside a single ``pump_lease`` call)."""

    def __init__(self, session_id: str, batches: list[TranscriptBatch]) -> None:
        self._session_id = session_id
        self._batches = list(batches)
        self.turns_since_calls: list[tuple[str, str | None, TranscriptPosition | None]] = []

    def turns_since(
        self, session_id: str, *, spawn_cwd: str | None, since: TranscriptPosition | None
    ) -> TranscriptBatch:
        self.turns_since_calls.append((session_id, spawn_cwd, since))
        if session_id != self._session_id or not self._batches:
            return TranscriptBatch(
                session_id=session_id,
                available=False,
                reason="not_found",
                turns=[],
                unlinked_sidechains=[],
                next_position=None,
                complete=True,
                truncated=False,
                sidechain_truncated=False,
                normalizer_version="fake/1",
                harness_version=None,
            )
        return self._batches.pop(0)

    def read_raw_lines(self, session_id: str, *, spawn_cwd: str | None) -> list[str]:
        return []

    def size_bytes(self, session_id: str, *, spawn_cwd: str | None) -> int | None:
        return None

    def context_tokens(self, session_id: str, *, spawn_cwd: str | None) -> int | None:
        return None


def test_pump_lease_drains_a_segment_across_several_incomplete_reads() -> None:
    """review round 7 F2: `TranscriptBatch.complete=False` means more remains RIGHT NOW —
    `pump_lease` must loop reading the same segment until it catches up, not stop after
    one window like `run()` does."""
    batches = [
        _incomplete_batch([_turn(0, "a")], next_token="pos-1"),
        _incomplete_batch([_turn(1, "b")], next_token="pos-2"),
        _batch([_turn(2, "c")], next_token="pos-3"),  # complete=True — the drain's last read
    ]
    source = _SequencedTranscriptSource("sess-a", batches)
    harness = FakeHarness(
        handle=WorkerHandle(session_id="sess-a", pid=1, process_start_time="1"), verdict=None, transcript_source=source
    )
    store = make_store("sqlite://")
    ctx = make_context(
        store,
        hub=FakeHub(),
        provider=FakeProvider({"e1": "/ws/e1"}),
        harness=harness,
        probe=FakeProbe(),
        config=LoopConfig(runner_id="r1", workspace_id="ws1", transcripts_ship=True),
    )
    segment_id = _spawn_one_segment(ctx)
    lease = ctx.store.active_lease("lease_1")
    assert lease is not None

    TranscriptPump(ctx).pump_lease(lease.lease_id, deadline=_NOW + timedelta(seconds=PUMP_LEASE_MAX_SECONDS))

    assert len(source.turns_since_calls) == 3  # all three reads drained in one call
    segment = ctx.store.transcript_segment(segment_id)
    assert segment is not None
    assert segment.cursor == "pos-3"
    assert segment.truncated_reason is None  # caught up — never marked incomplete
    pending = ctx.store.pending_transcript_outbound()
    assert len(pending) == 3


@dataclass
class _ClockAdvancingAfterNCallsSource:
    """Wraps a ``_SequencedTranscriptSource``, advancing ``clock`` by ``jump`` right after
    its ``after``-th call returns — pins exactly which read ``pump_lease``'s drain loop is
    mid-flight on when its deadline first reads as expired (F2), without coupling the test
    to how many internal ``.now()`` calls one ``_pump_one`` happens to make."""

    inner: _SequencedTranscriptSource
    clock: FixedClock
    jump: timedelta
    after: int

    def turns_since(
        self, session_id: str, *, spawn_cwd: str | None, since: TranscriptPosition | None
    ) -> TranscriptBatch:
        batch = self.inner.turns_since(session_id, spawn_cwd=spawn_cwd, since=since)
        if len(self.inner.turns_since_calls) == self.after:
            self.clock.advance(self.jump)
        return batch

    def read_raw_lines(self, session_id: str, *, spawn_cwd: str | None) -> list[str]:
        return self.inner.read_raw_lines(session_id, spawn_cwd=spawn_cwd)

    def size_bytes(self, session_id: str, *, spawn_cwd: str | None) -> int | None:
        return self.inner.size_bytes(session_id, spawn_cwd=spawn_cwd)

    def context_tokens(self, session_id: str, *, spawn_cwd: str | None) -> int | None:
        return self.inner.context_tokens(session_id, spawn_cwd=spawn_cwd)


def test_pump_lease_marks_incomplete_when_its_deadline_expires_mid_drain() -> None:
    """review round 7 F2: a deadline expiring mid-drain stops the loop where it is — the
    segment is marked truncated (the new incomplete-closure reason) and the fact-lane
    warning fires, rather than the remaining unread content vanishing once it finalizes."""
    batches = [
        _incomplete_batch([_turn(0, "a")], next_token="pos-1"),
        _incomplete_batch([_turn(1, "b")], next_token="pos-2"),
        _batch([_turn(2, "c")], next_token="pos-3"),
    ]
    clock = FixedClock(instant=_NOW)
    inner = _SequencedTranscriptSource("sess-a", batches)
    source = _ClockAdvancingAfterNCallsSource(inner, clock=clock, jump=timedelta(seconds=10), after=2)
    harness = FakeHarness(
        handle=WorkerHandle(session_id="sess-a", pid=1, process_start_time="1"), verdict=None, transcript_source=source
    )
    store = make_store("sqlite://")
    ctx = make_context(
        store,
        hub=FakeHub(),
        provider=FakeProvider({"e1": "/ws/e1"}),
        harness=harness,
        probe=FakeProbe(),
        config=LoopConfig(runner_id="r1", workspace_id="ws1", transcripts_ship=True),
        clock=clock,
    )
    segment_id = _spawn_one_segment(ctx)
    lease = ctx.store.active_lease("lease_1")
    assert lease is not None
    deadline = clock.now() + timedelta(seconds=5)

    TranscriptPump(ctx).pump_lease(lease.lease_id, deadline=deadline)

    assert len(inner.turns_since_calls) == 2  # the third, would-be-final read never happened
    segment = ctx.store.transcript_segment(segment_id)
    assert segment is not None
    assert segment.truncated_reason == "lease_closure_incomplete"
    assert segment.cursor == "pos-2"  # the first two reads still landed
    fact_events = ctx.store.pending_outbound()
    kinds = [json.loads(e.payload)["kind"] for e in fact_events]
    assert "transcript-truncated" in kinds


@dataclass
class _AdvanceClockAfterSessionSource:
    """Serves one complete batch per session; advances ``clock`` past ``deadline`` right
    after serving ``advance_after``'s own read — pins ``pump_lease``'s OUTER per-segment
    loop (not the per-segment drain loop) to see its deadline as expired before ever
    attempting the next segment (F2)."""

    batches: dict[str, TranscriptBatch]
    clock: FixedClock
    jump: timedelta
    advance_after: str
    turns_since_calls: list[tuple[str, str | None, TranscriptPosition | None]] = field(default_factory=list)

    def turns_since(
        self, session_id: str, *, spawn_cwd: str | None, since: TranscriptPosition | None
    ) -> TranscriptBatch:
        self.turns_since_calls.append((session_id, spawn_cwd, since))
        batch = self.batches.get(session_id)
        if session_id == self.advance_after:
            self.clock.advance(self.jump)
        if batch is None:
            return TranscriptBatch(
                session_id=session_id,
                available=False,
                reason="not_found",
                turns=[],
                unlinked_sidechains=[],
                next_position=None,
                complete=True,
                truncated=False,
                sidechain_truncated=False,
                normalizer_version="fake/1",
                harness_version=None,
            )
        return batch

    def read_raw_lines(self, session_id: str, *, spawn_cwd: str | None) -> list[str]:
        return []

    def size_bytes(self, session_id: str, *, spawn_cwd: str | None) -> int | None:
        return None

    def context_tokens(self, session_id: str, *, spawn_cwd: str | None) -> int | None:
        return None


def test_pump_lease_marks_a_second_segment_truncated_when_never_even_attempted() -> None:
    """review round 7 F2: the outer per-segment loop's own deadline-break used to drop a
    never-attempted segment silently. A lease with two open segments (a resume under a
    different session id) whose deadline expires right after the first now marks the second."""
    clock = FixedClock(instant=_NOW)
    source = _AdvanceClockAfterSessionSource(
        batches={"sess-a": _batch([_turn(0, "a")], next_token="pos-1")},
        clock=clock,
        jump=timedelta(seconds=10),
        advance_after="sess-a",
    )
    harness = FakeHarness(
        handle=WorkerHandle(session_id="sess-a", pid=1, process_start_time="1"), verdict=None, transcript_source=source
    )
    store = make_store("sqlite://")
    ctx = make_context(
        store,
        hub=FakeHub(),
        provider=FakeProvider({"e1": "/ws/e1"}),
        harness=harness,
        probe=FakeProbe(),
        config=LoopConfig(runner_id="r1", workspace_id="ws1", transcripts_ship=True),
        clock=clock,
    )
    segment_a_id = _spawn_one_segment(ctx)
    # A same-lease resume under a DIFFERENT session id leaves segment sess-a open too —
    # only a same-session resume finalizes it — so the lease now has two open segments.
    ctx.store.record_spawn(
        "lease_1", pid=2, process_start_time="2", session_id="sess-b", spawned_at=_NOW + timedelta(seconds=1)
    )
    lease = ctx.store.active_lease("lease_1")
    assert lease is not None
    open_segments = {
        s.session_id: s.segment_id for s in ctx.store.open_transcript_segments() if s.lease_id == lease.lease_id
    }
    assert set(open_segments) == {"sess-a", "sess-b"}
    segment_b_id = open_segments["sess-b"]
    deadline = clock.now() + timedelta(seconds=5)

    TranscriptPump(ctx).pump_lease(lease.lease_id, deadline=deadline)

    assert [c[0] for c in source.turns_since_calls] == ["sess-a"]  # sess-b never even attempted
    segment_a = ctx.store.transcript_segment(segment_a_id)
    segment_b = ctx.store.transcript_segment(segment_b_id)
    assert segment_a is not None
    assert segment_a.truncated_reason is None  # attempted and caught up
    assert segment_b is not None
    assert segment_b.truncated_reason == "lease_closure_incomplete"
    fact_events = ctx.store.pending_outbound()
    kinds = [json.loads(e.payload)["kind"] for e in fact_events]
    assert kinds.count("transcript-truncated") == 1


# --- review round 7 F3: the cursor-guard's early return must still warn a latched sidechain


def test_pump_warns_a_dropped_sidechain_even_when_the_cursor_guard_skips_the_segment() -> None:
    """review round 7 F3: `mark_sidechain_dropped_warned` latches (segment, agent_id) as
    warned the instant it's called, so the cursor-guard's early return must still fire
    that warning — or it's lost forever (no later tick re-latches the same pair)."""
    stuck = TranscriptBatch(
        session_id="sess-a",
        available=True,
        reason=None,
        turns=[_turn(0, "hi")],
        unlinked_sidechains=[_unlinked_sidechain("sub_1")],
        next_position=None,
        complete=True,
        truncated=False,
        sidechain_truncated=False,
        normalizer_version="fake/1",
        harness_version=None,
    )
    ctx, _source = _ctx(ship=True, batches={"sess-a": stuck})
    _spawn_one_segment(ctx)

    with capture_logs() as logs:
        TranscriptPump(ctx).run()

    _assert_skipped_not_raised(logs)
    assert ctx.store.pending_transcript_outbound() == []  # never enqueued — the guard still skips
    fact_events = ctx.store.pending_outbound()
    kinds = [json.loads(e.payload)["kind"] for e in fact_events]
    assert "transcript-sidechain-dropped" in kinds  # the already-latched warning still fires


# --- review round 7 F4: shrink recurses into a tool input's nested containers --------


def _multi_edit_tool_turn(index: int, *, old: str, new: str) -> NormalizedTurn:
    """A `MultiEdit`-shaped turn: `tool.input["edits"]` is a LIST of dicts, each carrying
    its own `old_string`/`new_string` — not a flat top-level string (F4)."""
    return NormalizedTurn(
        index=index,
        kind="tool",
        timestamp=_NOW,
        text="",
        tool=_tool_call("", input_={"edits": [{"old_string": old, "new_string": new}]}),
        thinking_redacted=False,
        sidechain=None,
        truncated=False,
    )


def test_pump_shrinks_a_multi_edit_shaped_tool_input_instead_of_emptying_the_record() -> None:
    """review round 7 F4: `MultiEdit.edits` nests its oversized strings below `tool.input`'s
    top-level keys — a flat walk counts their bytes toward the overshoot but never offers
    them as shrinkable. Mutation-verify by reverting to the flat top-level-only walk."""
    huge = "e" * (TRANSCRIPT_RECORD_MAX_BYTES // 2)
    turn = _multi_edit_tool_turn(0, old=huge, new=huge)
    ctx, _source = _ctx(ship=True, batches={"sess-a": _batch([turn], next_token="pos-1")})
    segment_id = _spawn_one_segment(ctx)

    TranscriptPump(ctx).run()

    pending = ctx.store.pending_transcript_outbound()
    assert len(pending) == 1
    assert len(pending[0].payload.encode("utf-8")) <= TRANSCRIPT_RECORD_MAX_BYTES
    body = json.loads(pending[0].payload)
    assert body["turns"] != []  # shrunk, not emptied — genuine content still shipped
    edit = body["turns"][0]["tool"]["input"]["edits"][0]
    assert len(edit["old_string"]) > 0
    assert len(edit["new_string"]) > 0
    assert body["turns"][0]["tool"]["input_truncated"] is True
    assert body["record_truncated"] is True
    segment = ctx.store.transcript_segment(segment_id)
    assert segment is not None
    assert segment.truncated_reason == "record_cap_exceeded"


def test_pump_shrinking_a_multi_edit_input_never_mutates_the_sources_own_turn() -> None:
    """A shallow ``dict(tool.input)`` still shares nested containers (``MultiEdit.edits``) with
    the harness batch's own ``ToolCall``, which the shrink pass then mutates in place."""
    huge = "e" * (TRANSCRIPT_RECORD_MAX_BYTES // 2)
    turn = _multi_edit_tool_turn(0, old=huge, new=huge)
    ctx, _source = _ctx(ship=True, batches={"sess-a": _batch([turn], next_token="pos-1")})
    _spawn_one_segment(ctx)

    TranscriptPump(ctx).run()

    assert turn.tool is not None
    source_edit = turn.tool.input["edits"][0]
    assert source_edit["old_string"] == huge  # untouched — full length, not shrunk in place
    assert source_edit["new_string"] == huge


# --- review round 7 F8: backpressure against an already-unbounded outbound buffer ----


def test_pump_gates_on_outstanding_buffered_bytes_before_reading_a_new_batch() -> None:
    """review round 7 F8: a prolonged hub outage leaves buffered content resident
    indefinitely — the pump must not pile more onto it. Transient backpressure, not a
    latch: it self-clears once the outstanding total drops back under the cap."""
    ctx, source = _ctx(ship=True, batches={"sess-a": _batch([_turn(0, "hi")], next_token="pos-1")})
    segment_id = _spawn_one_segment(ctx)
    over_cap_ctx = replace(
        ctx,
        store=StubbedBufferBytesStore(ctx.store, MAX_BUFFERED_BYTES),  # type: ignore[arg-type]
    )

    TranscriptPump(over_cap_ctx).run()

    assert source.turns_since_calls == []  # never even read this tick
    assert ctx.store.pending_transcript_outbound() == []
    segment = ctx.store.transcript_segment(segment_id)
    assert segment is not None
    assert segment.cursor is None  # never advanced

    under_cap_ctx = replace(
        ctx,
        store=StubbedBufferBytesStore(ctx.store, MAX_BUFFERED_BYTES - 1),  # type: ignore[arg-type]
    )
    TranscriptPump(under_cap_ctx).run()

    assert len(source.turns_since_calls) == 1  # resumed once back under the cap
    segment = ctx.store.transcript_segment(segment_id)
    assert segment is not None
    assert segment.cursor == "pos-1"


# --- Every path that returns without ever reading the source: a lease-closure pump this
# happens to has not "caught up", and its segment finalizes losing what the source held.


def test_pump_lease_marks_incomplete_when_backpressure_gates_the_close_time_read() -> None:
    """verify round 8 (F2 follow-up): F8's backpressure gate returning early looked
    identical to a caught-up segment to ``pump_lease``'s drain loop — the segment finalized
    with its content never even attempted, no truncated_reason, no fact-lane warning."""
    ctx, source = _ctx(ship=True, batches={"sess-a": _batch([_turn(0, "hi")], next_token="pos-1")})
    segment_id = _spawn_one_segment(ctx)
    lease = ctx.store.active_lease("lease_1")
    assert lease is not None
    gated_ctx = replace(
        ctx,
        store=StubbedBufferBytesStore(ctx.store, MAX_BUFFERED_BYTES),  # type: ignore[arg-type]
    )

    TranscriptPump(gated_ctx).pump_lease(lease.lease_id, deadline=_NOW + timedelta(seconds=PUMP_LEASE_MAX_SECONDS))

    assert source.turns_since_calls == []  # never even attempted
    segment = ctx.store.transcript_segment(segment_id)
    assert segment is not None
    assert segment.truncated_reason == "lease_closure_incomplete"
    fact_events = ctx.store.pending_outbound()
    kinds = [json.loads(e.payload)["kind"] for e in fact_events]
    assert "transcript-truncated" in kinds


def test_pump_lease_marks_incomplete_when_the_source_is_unavailable_at_closure() -> None:
    """An unscripted session reads as ``not_found``, the shape a real source reports on a race.
    ``_pump_one``'s ``not batch.available`` branch used to read as caught-up."""
    ctx, source = _ctx(ship=True, batches={})  # "sess-a" unscripted — reads not_found
    segment_id = _spawn_one_segment(ctx)
    lease = ctx.store.active_lease("lease_1")
    assert lease is not None

    TranscriptPump(ctx).pump_lease(lease.lease_id, deadline=_NOW + timedelta(seconds=PUMP_LEASE_MAX_SECONDS))

    assert len(source.turns_since_calls) == 1  # attempted, just came back unavailable
    segment = ctx.store.transcript_segment(segment_id)
    assert segment is not None
    assert segment.truncated_reason == "lease_closure_incomplete"
    fact_events = ctx.store.pending_outbound()
    kinds = [json.loads(e.payload)["kind"] for e in fact_events]
    assert "transcript-truncated" in kinds


def test_pump_lease_marks_incomplete_when_the_source_raises_at_closure() -> None:
    """That the raise does not propagate is pinned elsewhere; this pins that the segment it
    finalizes carries a trace of the read it never got."""
    store = make_store("sqlite://")
    harness = FakeHarness(
        handle=WorkerHandle(session_id="sess-a", pid=1, process_start_time="1"),
        verdict=None,
        transcript_source=_RaisingTranscriptSource(),
    )
    ctx = make_context(
        store,
        hub=FakeHub(),
        provider=FakeProvider({"e1": "/ws/e1"}),
        harness=harness,
        probe=FakeProbe(),
        config=LoopConfig(runner_id="r1", workspace_id="ws1", transcripts_ship=True),
    )
    segment_id = _spawn_one_segment(ctx)
    lease = ctx.store.active_lease("lease_1")
    assert lease is not None

    TranscriptPump(ctx).pump_lease(lease.lease_id, deadline=_NOW + timedelta(seconds=PUMP_LEASE_MAX_SECONDS))

    segment = ctx.store.transcript_segment(segment_id)
    assert segment is not None
    assert segment.truncated_reason == "lease_closure_incomplete"
    fact_events = ctx.store.pending_outbound()
    kinds = [json.loads(e.payload)["kind"] for e in fact_events]
    assert "transcript-truncated" in kinds


def test_pump_lease_marks_incomplete_when_the_cursor_is_stuck_at_closure() -> None:
    """Returning ``_CAUGHT_UP`` from the stuck-cursor guard lets a closing lease finalize its
    segment with no truncation trace though turns were read and discarded."""
    ctx, source = _ctx(ship=True)
    segment_id = _spawn_one_segment(ctx)
    # Stuck: turns present, `next_position=None` so `new_cursor` falls back to the
    # segment's own (still-`None`) cursor — the guard fires on the very first read too.
    source._batches["sess-a"] = TranscriptBatch(
        session_id="sess-a",
        available=True,
        reason=None,
        turns=[_turn(0, "first"), _turn(1, "second")],
        unlinked_sidechains=[],
        next_position=None,
        complete=True,
        truncated=False,
        sidechain_truncated=False,
        normalizer_version="fake/1",
        harness_version=None,
    )
    lease = ctx.store.active_lease("lease_1")
    assert lease is not None

    with capture_logs() as logs:
        TranscriptPump(ctx).pump_lease(lease.lease_id, deadline=_NOW + timedelta(seconds=PUMP_LEASE_MAX_SECONDS))

    _assert_skipped_not_raised(logs)
    assert len(source.turns_since_calls) == 1  # attempted once, then stopped rather than spinning
    assert ctx.store.pending_transcript_outbound() == []  # the stuck read shipped nothing
    segment = ctx.store.transcript_segment(segment_id)
    assert segment is not None
    assert segment.shipped_turns == 0  # nothing ever shipped — this is real loss, not caught-up
    assert segment.truncated_reason == "lease_closure_incomplete"
    fact_events = ctx.store.pending_outbound()
    kinds = [json.loads(e.payload)["kind"] for e in fact_events]
    assert "transcript-truncated" in kinds


# --- configured byte ceilings (blizzard#338) ----------------------------------------


def test_a_configured_chunk_budget_stops_shipping_where_the_default_would_not() -> None:
    """The whole point of the knob: the same already-shipped total that is nowhere near the
    64 MB default stops the lane once an operator narrows the budget to it."""
    ctx, source = _ctx(ship=True, batches={"sess-a": _batch([_turn(0, "hi")], next_token="pos-1")}, chunk_max_bytes=500)
    segment_id = _spawn_one_segment(ctx)
    ctx.store.record_transcript_deltas(
        segment_id=segment_id,
        chunk_id="ch_1",
        cursor=None,
        shipped_bytes=600,
        shipped_turns=0,
        normalizer_version="fake/1",
        harness_version=None,
        payloads=["{}"],
        created_at=_NOW,
    )

    TranscriptPump(ctx).run()

    segment = ctx.store.transcript_segment(segment_id)
    assert segment is not None
    assert segment.shipping_stopped_reason == "chunk_budget_exceeded"
    assert source.turns_since_calls == []  # the budget check still precedes the read


def test_a_widened_chunk_budget_ships_past_the_default_ceiling() -> None:
    """The backfill direction, which is what the knob exists for: a chunk already past the
    64 MB default keeps shipping under a widened budget rather than latching stopped."""
    ctx, _source = _ctx(
        ship=True,
        batches={"sess-a": _batch([_turn(0, "hi")], next_token="pos-1")},
        chunk_max_bytes=CHUNK_TRANSCRIPT_MAX_BYTES * 4,
    )
    segment_id = _spawn_one_segment(ctx)
    ctx.store.record_transcript_deltas(
        segment_id=segment_id,
        chunk_id="ch_1",
        cursor=None,
        shipped_bytes=CHUNK_TRANSCRIPT_MAX_BYTES + 1,
        shipped_turns=0,
        normalizer_version="fake/1",
        harness_version=None,
        payloads=["{}"],
        created_at=_NOW,
    )

    TranscriptPump(ctx).run()

    segment = ctx.store.transcript_segment(segment_id)
    assert segment is not None
    assert segment.shipping_stopped_reason is None
    assert segment.shipped_turns == 1


def test_a_configured_record_cap_shrinks_a_batch_the_default_would_ship_whole() -> None:
    """The per-record cap reaches `_build_records`, not just the chunk budget: a narrow cap
    marks the record truncated where the 8 MB default would have shipped the text intact."""
    ctx, _source = _ctx(
        ship=True,
        batches={"sess-a": _batch([_turn(0, "x" * 4000)], next_token="pos-1")},
        record_max_bytes=1500,
    )
    segment_id = _spawn_one_segment(ctx)

    TranscriptPump(ctx).run()

    [delta] = [d for d in ctx.store.pending_transcript_outbound(limit=10) if not d.final]
    payload = json.loads(delta.payload)
    assert payload["record_truncated"] is True
    assert len(payload["turns"][0]["text"]) < 4000
    segment = ctx.store.transcript_segment(segment_id)
    assert segment is not None
    assert segment.truncated_reason == "record_cap_exceeded"


def test_unconfigured_caps_leave_the_module_defaults_in_force() -> None:
    """The defaults are not restated by the config layer, so an unconfigured runner must
    still read exactly them — the property, not a copy of the number."""
    ctx, _source = _ctx(ship=True)
    pump = TranscriptPump(ctx)

    assert pump._record_max_bytes == TRANSCRIPT_RECORD_MAX_BYTES
    assert pump._chunk_max_bytes == CHUNK_TRANSCRIPT_MAX_BYTES


# --- cross-window correlation (blizzard#338) ----------------------------------------


def _shipped_turns(ctx) -> list[dict]:  # type: ignore[no-untyped-def]
    """Every turn the pump actually enqueued, in wire order across all its records."""
    return [
        turn
        for delta in ctx.store.pending_transcript_outbound(limit=50)
        if not delta.final
        for turn in json.loads(delta.payload)["turns"]
    ]


def test_a_result_whose_call_shipped_last_window_rides_as_an_output_patch() -> None:
    """Defect 2: the `tool_result` lands in a later window than its `tool_use`, so no turn in
    this batch can carry it. It ships by id instead of being dropped."""
    ctx, _source = _ctx(
        ship=True,
        batches={
            "sess-a": _batch([], next_token="pos-2", late_tool_outputs=[LateToolOutput("toolu_A", "the result", False)])
        },
    )
    _spawn_one_segment(ctx)

    TranscriptPump(ctx).run()

    [patch] = [t for t in _shipped_turns(ctx) if t["tool"] and t["tool"]["output_patch"]]
    assert patch["kind"] == "tool"
    assert patch["tool"]["tool_use_id"] == "toolu_A"
    assert patch["tool"]["output"] == "the result"


def test_a_sidechain_links_by_a_pair_a_previous_window_persisted() -> None:
    """Defect 1, and the reason the ledger column exists: the window holding the sidecar knows
    no pair at all — only the segment's own accumulated map still names the parent."""
    ctx, _source = _ctx(
        ship=True,
        batches={"sess-a": _batch([], next_token="pos-2", unlinked_sidechains=[_unlinked_sidechain("agent-7")])},
    )
    segment_id = _spawn_one_segment(ctx)
    ctx.store.advance_transcript_cursor(
        segment_id,
        cursor="pos-1",
        normalizer_version="fake/1",
        harness_version=None,
        agent_tool_use_ids={"agent-7": "toolu_TASK"},
    )

    TranscriptPump(ctx).run()

    [linked] = [t for t in _shipped_turns(ctx) if t["kind"] == "sidechain"]
    assert linked["sidechain"]["parent_tool_use_id"] == "toolu_TASK"
    assert linked["sidechain"]["link"] == "agent-id-late"
    assert linked["sidechain"]["agent_id"] == "agent-7"


def test_a_pair_learned_this_window_is_persisted_for_the_next_one() -> None:
    """The write that makes the test above possible — and it rides the SAME call as the cursor,
    so a crash cannot leave a pair remembered for content the segment never read."""
    ctx, _source = _ctx(
        ship=True,
        batches={"sess-a": _batch([_turn(0, "hi")], next_token="pos-2", agent_tool_use_ids={"agent-7": "toolu_T"})},
    )
    segment_id = _spawn_one_segment(ctx)

    TranscriptPump(ctx).run()

    segment = ctx.store.transcript_segment(segment_id)
    assert segment is not None
    assert segment.agent_tool_use_ids == {"agent-7": "toolu_T"}


def test_a_sidechain_with_no_pair_anywhere_is_still_dropped_and_warned() -> None:
    """The residual case the fix does not close: nothing ever named this agent's parent, so
    there is no id to link by and the warning stays truthful."""
    ctx, _source = _ctx(
        ship=True,
        batches={
            "sess-a": _batch([_turn(0, "hi")], next_token="pos-2", unlinked_sidechains=[_unlinked_sidechain("ghost")])
        },
    )
    _spawn_one_segment(ctx)

    TranscriptPump(ctx).run()

    assert [t for t in _shipped_turns(ctx) if t["kind"] == "sidechain"] == []
    kinds = [json.loads(e.payload)["kind"] for e in ctx.store.pending_outbound()]
    assert "transcript-sidechain-dropped" in kinds


def test_a_linked_sidechain_no_longer_warns_as_dropped() -> None:
    """The warning must not survive its own cause: an operator who keeps seeing it after the
    fix has no way to tell a real residual case from noise."""
    ctx, _source = _ctx(
        ship=True,
        batches={
            "sess-a": _batch(
                [_turn(0, "hi")],
                next_token="pos-2",
                unlinked_sidechains=[_unlinked_sidechain("agent-7")],
                agent_tool_use_ids={"agent-7": "toolu_T"},
            )
        },
    )
    _spawn_one_segment(ctx)

    TranscriptPump(ctx).run()

    kinds = [json.loads(e.payload)["kind"] for e in ctx.store.pending_outbound()]
    assert "transcript-sidechain-dropped" not in kinds


def test_synthesized_turns_advance_the_range_so_the_next_window_does_not_overlap() -> None:
    """The hub's natural key is `(segment_id, turn_range_start)`: a synthesized turn the count
    ignores would put the next window's record on a range this one already claimed."""
    ctx, _source = _ctx(
        ship=True,
        batches={
            "sess-a": _batch(
                [_turn(0, "hi")],
                next_token="pos-2",
                late_tool_outputs=[LateToolOutput("toolu_A", "out", False)],
                unlinked_sidechains=[_unlinked_sidechain("agent-7")],
                agent_tool_use_ids={"agent-7": "toolu_T"},
            )
        },
    )
    segment_id = _spawn_one_segment(ctx)

    TranscriptPump(ctx).run()

    segment = ctx.store.transcript_segment(segment_id)
    assert segment is not None
    assert segment.shipped_turns == 3  # the real turn, the output patch, the sidechain
    assert [t["index"] for t in _shipped_turns(ctx)] == [0, 1, 2]

"""The transcript lane's pump (component tier, issue #246) — cursor advance, the 1 MB
per-record and 64 MB per-chunk caps (D4), the ``ship`` off-switch (D5), and blizzard#247's
turn-range wire shape."""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta

import pytest
from structlog.testing import capture_logs

from blizzard.foundation.clock import FixedClock
from blizzard.runner.harness.adapter import WorkerHandle
from blizzard.runner.harness.transcript import (
    NormalizedTurn,
    SidechainConversation,
    ToolCall,
    TranscriptBatch,
    TranscriptPosition,
)
from blizzard.runner.loop.attempt import FAILED, Attempt
from blizzard.runner.loop.context import LoopConfig
from blizzard.runner.loop.transcript_pump import (
    CHUNK_TRANSCRIPT_MAX_BYTES,
    PUMP_LEASE_MAX_SECONDS,
    TRANSCRIPT_RECORD_MAX_BYTES,
    TranscriptPump,
)
from blizzard.runner.store.repository import NewLease
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
    )


def _unlinked_sidechain(agent_id: str) -> SidechainConversation:
    return SidechainConversation(
        agent_id=agent_id, agent_type="general", link="unlinked", turns=[_turn(0, "orphaned subagent turn")]
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


def test_pump_ships_an_explicit_empty_slice_when_shrinking_cannot_close_the_gap() -> None:
    """review F2: once every shrinkable field is empty, structural overhead alone can still
    exceed the cap — an explicit empty-turns slice over the claimed range, never a
    still-over-cap enqueue. The range stays gapless even though the content is lost."""
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
    assert (body["turn_range_start"], body["turn_range_end"]) == (0, len(many_turns) - 1)
    # review F5: this record is accepted outright by every hub-side cap (it's tiny once
    # emptied) — `record_truncated` is the ONLY wire signal that it lost its claimed span.
    assert body["record_truncated"] is True
    segment = ctx.store.transcript_segment(segment_id)
    assert segment is not None
    assert segment.cursor == "pos-1"  # still advances — never re-reads the batch
    assert segment.shipped_turns == len(many_turns)  # the range is claimed even though empty
    assert segment.truncated_reason == "record_unshippable"
    assert segment.shipping_stopped_reason is None  # transient, not a stop-shipping latch


def test_pump_shrinks_a_batch_with_many_large_shrinkable_fields() -> None:
    """A batch whose oversized content spans many turns — a real catch-up window's shape —
    must converge under the cap, not get dropped whole. Regression for a fixed-iteration
    loop that only ever touched the single largest field per pass."""
    many_turns = [_tool_turn(i, output="x" * 30_000) for i in range(300)]
    ctx, _source = _ctx(ship=True, batches={"sess-a": _batch(many_turns, next_token="pos-1")})
    segment_id = _spawn_one_segment(ctx)

    TranscriptPump(ctx).run()

    pending = ctx.store.pending_transcript_outbound()
    assert len(pending) == 1
    assert len(pending[0].payload.encode("utf-8")) <= TRANSCRIPT_RECORD_MAX_BYTES
    body = json.loads(pending[0].payload)
    assert len(body["turns"]) == len(many_turns)  # every turn survives, shrunk, not dropped
    # review F6: every turn present with its content wiped to "" also passes the assertion
    # above — assert real bytes survive, not just turn count.
    retained = sum(len(t["tool"]["output"]) for t in body["turns"])
    assert retained > 0
    segment = ctx.store.transcript_segment(segment_id)
    assert segment is not None
    assert segment.truncated_reason == "record_cap_exceeded"
    assert segment.cursor == "pos-1"


def test_pump_shrinks_a_severely_oversized_batch_without_emptying_every_field() -> None:
    """review F1: a window many times over cap — the ordinary shape of a real catch-up
    read, not a pathological one — must retain a share of its content, never empty every
    field in the first pass. Mirrors the finding's own ~8 MB / 50-turn measurement."""
    window_turns = [_tool_turn(i, output="x" * 160_000) for i in range(50)]  # ~8 MB of output
    ctx, _source = _ctx(ship=True, batches={"sess-a": _batch(window_turns, next_token="pos-1")})
    segment_id = _spawn_one_segment(ctx)

    TranscriptPump(ctx).run()

    pending = ctx.store.pending_transcript_outbound()
    assert len(pending) == 1
    assert len(pending[0].payload.encode("utf-8")) <= TRANSCRIPT_RECORD_MAX_BYTES
    body = json.loads(pending[0].payload)
    assert len(body["turns"]) == len(window_turns)  # never drops a turn
    retained = sum(len(t["tool"]["output"]) for t in body["turns"])
    # Not literally 0, and not a token sliver either — the budget allows roughly cap-many
    # bytes of content; demand at least a tenth of the cap survives, not just a nonzero byte.
    assert retained > TRANSCRIPT_RECORD_MAX_BYTES * 0.1
    segment = ctx.store.transcript_segment(segment_id)
    assert segment is not None
    assert segment.truncated_reason == "record_cap_exceeded"
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
    """review round 6 F1: the shrink budget (``size``/``overshoot``) is measured in
    ``json.dumps``-escaped bytes, but the old ``shrinkable`` sum used raw Python ``len()``
    — for non-ASCII text (``ensure_ascii=True`` escapes each CJK character to 6 bytes),
    that unit mismatch clamped ``keep_fraction`` to 0.0 and emptied the field on the first
    pass, even though the record is only modestly (~18%) over cap. Mirrors the finding's
    own repro: a CJK-heavy field, ~1.24 MB serialized against the 1 MB cap."""
    huge_content = "文" * 206_000  # ~1.24 MB serialized (escaped) — ~18% over the 1 MB cap
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


def test_pump_shrinks_many_medium_tool_inputs_instead_of_emptying_the_record() -> None:
    """Many ordinary `Edit`-shaped calls, each individually under cap, summing well over it
    — the batch must converge to a shrunk, non-empty result, not fall into the
    structural-overhead empty-slice branch with nothing found to shrink."""
    edits = [_input_tool_turn(i, content="e" * 20_000) for i in range(60)]  # 60 * 20 KB > 1 MB
    ctx, _source = _ctx(ship=True, batches={"sess-a": _batch(edits, next_token="pos-1")})
    segment_id = _spawn_one_segment(ctx)

    TranscriptPump(ctx).run()

    pending = ctx.store.pending_transcript_outbound()
    assert len(pending) == 1
    assert len(pending[0].payload.encode("utf-8")) <= TRANSCRIPT_RECORD_MAX_BYTES
    body = json.loads(pending[0].payload)
    assert len(body["turns"]) == len(edits)  # every turn survives, shrunk, not dropped
    retained = sum(len(t["tool"]["input"]["content"]) for t in body["turns"])
    assert retained > 0
    segment = ctx.store.transcript_segment(segment_id)
    assert segment is not None
    assert segment.truncated_reason == "record_cap_exceeded"
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
    """review round 6 F3: `IHarnessTranscriptSource` permits a batch with turns but no
    `next_position` — the pump must not enqueue a record it can never advance the cursor
    past, or the next tick re-reads and re-ships the exact same turns forever. A bare
    `assert` guarding this is stripped under `python -O`; the guard must be a real
    conditional that skips the segment and logs, never raises."""
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
    """The same stuck source against a segment that HAS pumped before: `new_cursor` falls
    back to its own non-null cursor, so a null-check alone passes and the pump would
    re-ship the identical turns every tick. The guard is that the cursor CHANGED, not
    that it exists — and (review round 6 F3) skips the segment rather than raising, so
    the loop's other segments (and the drain's own buffered flush) still proceed."""
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
    ctx.store.record_transcript_delta(
        segment_id=segment_id,
        chunk_id="ch_1",
        cursor=None,
        shipped_bytes=CHUNK_TRANSCRIPT_MAX_BYTES,
        shipped_turns=0,
        normalizer_version="fake/1",
        harness_version=None,
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
    # A version WAS learned above (the seeded delta carried one), so closure still ships a
    # real final marker despite the budget stop.
    pending = ctx.store.pending_transcript_outbound()
    assert any(d.final for d in pending)


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
    ctx.store.record_transcript_delta(
        segment_id=segment_id,
        chunk_id="ch_1",
        cursor=None,
        shipped_bytes=CHUNK_TRANSCRIPT_MAX_BYTES - 10,
        shipped_turns=0,
        normalizer_version="fake/1",
        harness_version=None,
        payload="{}",
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


def test_run_isolates_one_segments_pump_failure_from_the_rest() -> None:
    """review round 6 F2: `run()`'s segment loop had no per-segment `try`, so one
    segment's own pump failure propagated out of `run()` entirely, aborting the loop
    before a later segment was even attempted (and, via `TranscriptDrain._run_unsafe`,
    skipping that tick's own buffered-record flush). One bad segment must not stop the
    others in the same run."""
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

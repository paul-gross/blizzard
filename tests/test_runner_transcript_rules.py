"""Pure rules of the transcript lane — the segment's transition table and derived state, the
truncation vocabulary and mark, the pump's window decisions, the backfill's and re-ship's
decisions, home selection and segment windowing. No store, no clock."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest

from blizzard.runner.harness.identity import SessionReference
from blizzard.runner.transcripts.archived_repository import ArchivedTranscript
from blizzard.runner.transcripts.backfill import (
    ReshipRefused,
    SegmentOpening,
    TranscriptReshipError,
    classify_backfill,
    newest_superseder,
    require_reshippable,
    resumable_for,
    unfinished,
)
from blizzard.runner.transcripts.home import (
    ResolvedTranscript,
    home_is_local,
    segment_window,
    session_start_cursor,
)
from blizzard.runner.transcripts.ledger import (
    CHUNK_BUDGET_EXCEEDED,
    SEGMENT_TRANSITIONS,
    TranscriptBackfillLease,
    TranscriptSegmentState,
    TruncationMark,
    TruncationReason,
)
from blizzard.runner.transcripts.repository import MAX_TURNS, Transcript, Turn, recent_window
from blizzard.runner.transcripts.shipping import after_window, plan_window, pre_read

pytestmark = pytest.mark.unit

_T0 = datetime(2026, 1, 1, tzinfo=UTC)


def _segment(**overrides: object) -> TranscriptSegmentState:
    fields: dict[str, object] = {
        "segment_id": "seg_1",
        "chunk_id": "ch_1",
        "node_id": "nd_build",
        "epoch": 1,
        "generation": 1,
        "lease_id": "lease_1",
        "session_id": "sess-a",
        "cursor": None,
        "shipped_bytes": 0,
        "shipped_turns": 0,
        "normalizer_version": "v1",
        "harness_version": None,
        "truncated_reason": None,
        "shipping_stopped_reason": None,
        "supersedes": None,
        "finalized_at": None,
        "stamped_at": _T0,
        "harness_id": "claude_code",
        "model": None,
        "effort": None,
        "spawn_cwd": "/ws/e1",
    }
    fields.update(overrides)
    return TranscriptSegmentState(**fields)  # type: ignore[arg-type]


def _turn(text: str) -> Turn:
    return Turn(
        index=0,
        kind="env",
        timestamp=_T0,
        text=text,
        tool=None,
        thinking_redacted=False,
        sidechain=None,
        truncated=False,
    )


def _transcript(*texts: str, available: bool = True, truncated: bool = False) -> Transcript:
    return Transcript(
        session_id="sess-a",
        available=available,
        reason=None if available else "not_found",
        turns=[_turn(t) for t in texts] if available else [],
        truncated=truncated,
    )


# --- the segment --------------------------------------------------------------------


def test_segment_transition_table() -> None:
    assert {
        "open": frozenset({"ship", "stop_shipping", "finalize", "mark_truncated"}),
        "finalized": frozenset({"mark_truncated"}),
    } == SEGMENT_TRANSITIONS
    sealed = _segment(finalized_at=_T0)
    assert [sealed.accepts(t) for t in ("ship", "stop_shipping", "finalize", "mark_truncated")] == [
        False,
        False,
        False,
        True,
    ]


def test_finalized_segment_accepts_no_content() -> None:
    assert _segment().accepts_content is True
    assert _segment(finalized_at=_T0).accepts_content is False


def test_stop_shipping_finalized_is_noop() -> None:
    assert _segment(finalized_at=_T0).accepts("stop_shipping") is False


def test_segment_final_and_truncated() -> None:
    assert (_segment().final, _segment().truncated) == (False, False)
    assert _segment(finalized_at=_T0).final is True
    assert _segment(truncated_reason="record_cap_exceeded").truncated is True


def test_stopped_segment_reads_truncated() -> None:
    assert _segment(shipping_stopped_reason=CHUNK_BUDGET_EXCEEDED).truncated is True


def test_lost_to_cap() -> None:
    assert _segment(truncated_reason=TruncationReason.HUB_CAPPED).lost_to_cap is True
    assert _segment(shipping_stopped_reason=CHUNK_BUDGET_EXCEEDED).lost_to_cap is True
    assert _segment(truncated_reason=TruncationReason.RECORD_CAP_EXCEEDED).lost_to_cap is False


# --- truncation ---------------------------------------------------------------------


def test_hub_capped_ranks_worst() -> None:
    assert [r.severity for r in TruncationReason] == [0, 1, 2, 3, 3, 4]
    assert max(TruncationReason, key=lambda r: r.severity) is TruncationReason.HUB_CAPPED


def test_truncation_mark_worst_of_warns_once() -> None:
    blank = TruncationMark(current_reason=None, current_severity=None, reasons_warned=())
    first = blank.apply("record_cap_exceeded", 1)
    assert (first.truncated_reason, first.truncated_reason_severity, first.reasons_warned, first.newly_warned) == (
        "record_cap_exceeded",
        1,
        ("record_cap_exceeded",),
        True,
    )

    marked = TruncationMark("record_unshippable", 2, ("record_unshippable",))
    milder = marked.apply("source_read_truncated", 0)
    assert (milder.truncated_reason, milder.reasons_warned, milder.newly_warned) == (
        None,
        ("record_unshippable", "source_read_truncated"),
        True,
    )

    again = marked.apply("record_unshippable", 2)
    assert (again.changes, again.newly_warned) == (False, False)


def test_truncation_mark_replaces_an_incomparable_current() -> None:
    legacy = TruncationMark("hub_capped", None, ("hub_capped",))
    assert legacy.apply("source_read_truncated", 0).truncated_reason == "source_read_truncated"


# --- the pump's window decisions ----------------------------------------------------


def _pre_read(segment: TranscriptSegmentState, *, shipped: int = 0, outstanding: int = 0):  # type: ignore[no-untyped-def]
    return pre_read(
        segment, chunk_shipped_bytes=shipped, chunk_max_bytes=100, outstanding_bytes=outstanding, max_buffered_bytes=50
    )


def test_stopped_segment_ships_nothing() -> None:
    gate = _pre_read(_segment(shipping_stopped_reason=CHUNK_BUDGET_EXCEEDED), shipped=1000, outstanding=1000)
    assert (gate.action, gate.outcome) == ("skip", "caught_up")


def test_pre_read_finalized_is_not_attempted() -> None:
    assert (_pre_read(_segment(finalized_at=_T0)).action, _pre_read(_segment(finalized_at=_T0)).outcome) == (
        "skip",
        "not_attempted",
    )


def test_pre_read_budget_spent_stops() -> None:
    gate = _pre_read(_segment(), shipped=100)
    assert (gate.action, gate.outcome, gate.stop_reason) == ("stop", "caught_up", CHUNK_BUDGET_EXCEEDED)


def test_pre_read_backpressure_skips() -> None:
    gate = _pre_read(_segment(), outstanding=50)
    assert (gate.action, gate.outcome) == ("skip", "not_attempted")
    assert _pre_read(_segment(), shipped=99, outstanding=49).action == "read"


def _plan(**overrides: object):  # type: ignore[no-untyped-def]
    fields: dict[str, object] = {
        "cursor": "c1",
        "new_cursor": "c2",
        "has_content": True,
        "total_bytes": 10,
        "chunk_shipped_bytes": 0,
        "chunk_max_bytes": 100,
        "complete": True,
    }
    fields.update(overrides)
    return plan_window(**fields)  # type: ignore[arg-type]


def test_plan_window_without_content() -> None:
    assert (_plan(has_content=False).action, _plan(has_content=False).outcome) == ("advance_cursor", "caught_up")
    assert _plan(has_content=False, new_cursor="c1", complete=False).action == "nothing"
    assert _plan(has_content=False, new_cursor="c1", complete=False).outcome == "incomplete"


def test_plan_window_unmoved_cursor_is_stuck() -> None:
    assert (_plan(new_cursor="c1").action, _plan(new_cursor="c1").outcome) == ("stuck", "stuck")
    assert _plan(new_cursor=None).action == "stuck"


def test_plan_window_all_or_nothing_budget() -> None:
    over = _plan(chunk_shipped_bytes=95, total_bytes=10)
    assert (over.action, over.outcome, over.stop_reason) == ("stop", "caught_up", CHUNK_BUDGET_EXCEEDED)
    exact = _plan(chunk_shipped_bytes=90, total_bytes=10, complete=False)
    assert (exact.action, exact.outcome) == ("ship", "incomplete")


def test_after_window_deadline_is_incomplete() -> None:
    assert after_window("caught_up", deadline_passed=True) == "read_to_end"
    assert after_window("incomplete", deadline_passed=True) == "incomplete"
    assert after_window("incomplete", deadline_passed=False) == "continue"
    assert after_window("stuck", deadline_passed=False) == "incomplete"


def test_unattempted_segment_is_closure_incomplete() -> None:
    assert after_window("not_attempted", deadline_passed=False) == "incomplete"


# --- the backfill and the re-ship ---------------------------------------------------


def _lease(*, has_segment: bool = False) -> TranscriptBackfillLease:
    return TranscriptBackfillLease(
        lease_id="lease_1",
        chunk_id="ch_1",
        node_id="nd_build",
        epoch=1,
        session_id="sess-a",
        has_segment=has_segment,
        harness_id="claude_code",
    )


def test_classify_backfill_already_present_wins() -> None:
    assert classify_backfill(_lease(has_segment=True), readable=False, imported=9, limit=1, backpressured=True) == (
        "already_present"
    )


def test_classify_backfill_unreadable_is_gone() -> None:
    assert classify_backfill(_lease(), readable=False, imported=9, limit=1, backpressured=True) == "gone"


def test_classify_backfill_defers_past_the_limit_or_under_backpressure() -> None:
    assert classify_backfill(_lease(), readable=True, imported=1, limit=1, backpressured=False) == "deferred"
    assert classify_backfill(_lease(), readable=True, imported=0, limit=1, backpressured=True) == "deferred"
    assert classify_backfill(_lease(), readable=True, imported=0, limit=None, backpressured=False) == "import"


def test_unfinished_excludes_active_leases() -> None:
    mine, live = _segment(segment_id="seg_1"), _segment(segment_id="seg_2", lease_id="lease_live")
    assert unfinished([mine, live], {"lease_live"}) == [mine]


def test_resumable_for_finds_an_earlier_reships_open_segment() -> None:
    source = _segment(finalized_at=_T0)
    left_open = _segment(segment_id="seg_2", supersedes="seg_1")
    other_session = _segment(segment_id="seg_3", session_id="sess-b")
    assert resumable_for(source, [source, other_session, left_open]) == left_open
    assert resumable_for(source, [source]) is None


def test_reship_refused_on_active_lease() -> None:
    with pytest.raises(ReshipRefused, match="still active"):
        require_reshippable(_segment(finalized_at=_T0), lease_active=True, chunk_shipped_bytes=0, chunk_max_bytes=10)


def test_reship_refuses_unfinished_source() -> None:
    with pytest.raises(ReshipRefused, match="finish it first: blizzard runner transcript backfill"):
        require_reshippable(_segment(), lease_active=False, chunk_shipped_bytes=0, chunk_max_bytes=10)


def test_reship_refused_when_budget_spent() -> None:
    with pytest.raises(TranscriptReshipError, match="spent its transcript budget"):
        require_reshippable(_segment(finalized_at=_T0), lease_active=False, chunk_shipped_bytes=10, chunk_max_bytes=10)
    require_reshippable(_segment(finalized_at=_T0), lease_active=False, chunk_shipped_bytes=9, chunk_max_bytes=10)


def test_reship_chain_stays_linear() -> None:
    source = _segment(segment_id="seg_1", finalized_at=_T0)
    first = _segment(segment_id="seg_2", supersedes="seg_1", finalized_at=_T0, stamped_at=_T0 + timedelta(hours=1))
    second = _segment(segment_id="seg_3", supersedes="seg_2", finalized_at=_T0, stamped_at=_T0 + timedelta(hours=2))
    still_open = _segment(segment_id="seg_4", supersedes="seg_3", stamped_at=_T0 + timedelta(hours=3))
    assert newest_superseder(source, [source, first, second, still_open]) == second
    assert newest_superseder(source, [source]) == source


def test_reship_opening_supersedes_source() -> None:
    target = _segment(segment_id="seg_2", generation=3, epoch=2, finalized_at=_T0)
    assert SegmentOpening.superseding(target) == SegmentOpening(
        chunk_id="ch_1",
        node_id="nd_build",
        epoch=2,
        generation=3,
        lease_id="lease_1",
        session=SessionReference("claude_code", "sess-a"),
        supersedes="seg_2",
        spawn_cwd="/ws/e1",
    )


def test_merged_import_opens_the_first_generation() -> None:
    opening = SegmentOpening.merged_import(_lease(), spawn_cwd=None)
    assert (opening.generation, opening.supersedes, opening.spawn_cwd) == (1, None, None)


# --- home selection and windowing ---------------------------------------------------


def test_home_is_local_while_open_or_unshipped() -> None:
    assert home_is_local(lease_active=True, unshipped=False) is True
    assert home_is_local(lease_active=False, unshipped=True) is True
    assert home_is_local(lease_active=False, unshipped=False) is False


def test_spawning_transcript() -> None:
    assert Transcript.spawning() == Transcript(
        session_id=None, available=False, reason="spawning", turns=[], truncated=False
    )


def test_archive_answers_only_when_found_with_turns() -> None:
    found = ArchivedTranscript(status="found", turns=[_turn("hub")], truncated=True)
    resolved = ResolvedTranscript.from_archive("sess-a", found)
    assert resolved is not None
    assert (resolved.provenance, resolved.transcript.truncated, resolved.hub_unreachable) == ("archived", True, False)
    assert ResolvedTranscript.from_archive("sess-a", replace(found, turns=[])) is None


def test_local_fallback_marks_hub_unreachable() -> None:
    unreachable = ArchivedTranscript(status="unreachable", turns=[], truncated=False)
    gone = _transcript(available=False)
    assert ResolvedTranscript.local_fallback(gone, unreachable).hub_unreachable is True
    unreadable = replace(gone, reason="unreadable")
    assert ResolvedTranscript.local_fallback(unreadable, unreachable).hub_unreachable is False


def test_session_start_cursor_is_the_preceding_siblings() -> None:
    first = _segment(segment_id="seg_1", cursor="c1")
    other = _segment(segment_id="seg_x", session_id="sess-b", cursor="cx")
    second = _segment(segment_id="seg_2", cursor="c2")
    assert session_start_cursor(first, [first, other, second]) is None
    assert session_start_cursor(second, [first, other, second]) == "c1"


def test_segment_window_trims_final_tail() -> None:
    sealed = _segment(finalized_at=_T0, cursor="c1")
    content = segment_window(sealed, _transcript("a", "b", "c"), _transcript("c"))
    assert ([t.text for t in content.turns], content.final, content.truncated) == (["a", "b"], True, False)


def test_segment_window_reports_a_gone_file_truncated() -> None:
    content = segment_window(_segment(), _transcript(available=False), None)
    assert (content.available, content.truncated, content.turns) == (False, True, [])


def test_segment_window_truncated_by_a_stop() -> None:
    stopped = _segment(shipping_stopped_reason=CHUNK_BUDGET_EXCEEDED)
    assert segment_window(stopped, _transcript("a"), None).truncated is True


def test_recent_window_keeps_last_max_turns() -> None:
    assert recent_window([1, 2, 3, 4], max_turns=2) == ([3, 4], True)
    assert recent_window([1, 2], max_turns=2) == ([1, 2], False)
    assert MAX_TURNS == 1000

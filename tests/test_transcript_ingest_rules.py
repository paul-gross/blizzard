"""Transcript ingest decisions by value: the cap ladder, the natural-key write, replay, and the folds (unit tier)."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from blizzard.hub.domain.observability.transcripts import (
    REJECTED_CHUNK_BUDGET_EXCEEDED,
    REJECTED_RECORD_TOO_LARGE,
    REJECTED_RUNNER_DAILY_RATE_EXCEEDED,
    CapBreach,
    NaturalKeyState,
    SegmentRecordContent,
    SegmentWrite,
    TranscriptCaps,
    TranscriptSlice,
    adjudicates,
    records_final,
    records_truncated,
    replay_outcome,
    segment_write,
    stored_turns,
)

pytestmark = pytest.mark.unit

_CAPS = TranscriptCaps(record_max_bytes=10, chunk_budget_max_bytes=100, runner_daily_rate_max_bytes=1000)


def test_a_record_over_its_ceiling_breaches_the_record_cap() -> None:
    assert _CAPS.record_breach(11) == CapBreach(REJECTED_RECORD_TOO_LARGE, 11, 10)
    assert _CAPS.record_breach(10) is None


def test_the_chunk_budget_counts_stored_bytes_plus_the_record() -> None:
    assert _CAPS.chunk_breach(5, chunk_stored=96) == CapBreach(REJECTED_CHUNK_BUDGET_EXCEEDED, 101, 100)
    assert _CAPS.chunk_breach(5, chunk_stored=95) is None


def test_the_runner_rate_counts_the_window_plus_the_record() -> None:
    assert _CAPS.runner_breach(5, runner_window=996) == CapBreach(REJECTED_RUNNER_DAILY_RATE_EXCEEDED, 1001, 1000)
    assert _CAPS.runner_breach(5, runner_window=995) is None


def test_the_runner_rate_window_opens_a_day_before_the_record_arrives() -> None:
    assert TranscriptCaps.window_start(datetime(2026, 3, 2, 9, tzinfo=UTC)) == datetime(2026, 3, 1, 9, tzinfo=UTC)


def test_a_records_byte_count_is_its_turns_utf8_bytes() -> None:
    record = TranscriptSlice(
        segment_id="s",
        chunk_id="c",
        node_id="n",
        epoch=1,
        spawn_generation=0,
        runner_id="r",
        turn_range_start=0,
        turn_range_end=1,
        final=False,
        normalizer_version="1",
        harness_version=None,
        record_truncated=False,
        turns_json='["é"]',
    )
    assert record.byte_count == 6


@pytest.mark.parametrize(
    ("state", "reason", "write"),
    [
        ("accepted", None, SegmentWrite.KEEP),
        ("accepted", REJECTED_RECORD_TOO_LARGE, SegmentWrite.KEEP),
        ("rejected", None, SegmentWrite.UPDATE_TO_ACCEPTED),
        ("rejected", REJECTED_RECORD_TOO_LARGE, SegmentWrite.UPDATE_STILL_REJECTED),
        ("absent", None, SegmentWrite.INSERT_ACCEPTED),
        ("absent", REJECTED_RECORD_TOO_LARGE, SegmentWrite.INSERT_REJECTED),
    ],
)
def test_the_natural_key_state_and_cap_verdict_pick_the_write(
    state: NaturalKeyState, reason: str | None, write: SegmentWrite
) -> None:
    assert segment_write(state, reason) is write


def test_only_an_accepted_key_skips_the_cap_ladder() -> None:
    assert [adjudicates(s) for s in ("absent", "accepted", "rejected")] == [True, False, True]


def test_a_write_reports_stored_unless_it_ends_rejected() -> None:
    assert {w for w in SegmentWrite if w.stored} == {
        SegmentWrite.KEEP,
        SegmentWrite.INSERT_ACCEPTED,
        SegmentWrite.UPDATE_TO_ACCEPTED,
    }


def test_a_replayed_seq_reports_its_recorded_decision_and_applies_only_when_absent() -> None:
    assert replay_outcome("rejected") == "capped"
    assert replay_outcome("accepted") == "already_applied"
    assert replay_outcome("absent") == "apply"


def _content(*, final: bool = False, rejected: bool = False, record_truncated: bool = False) -> SegmentRecordContent:
    return SegmentRecordContent(
        turn_range_start=0,
        turn_range_end=1,
        final=final,
        rejected=rejected,
        record_truncated=record_truncated,
        turns_json="[]" if rejected else '[{"i": 1}]',
    )


def test_a_segment_is_truncated_iff_any_record_was_rejected_or_declared_truncated() -> None:
    assert records_truncated([_content(), _content()]) is False
    assert records_truncated([_content(), _content(rejected=True)]) is True
    assert records_truncated([_content(record_truncated=True)]) is True


def test_a_segment_is_final_iff_any_record_closed_it() -> None:
    assert records_final([_content(), _content(final=True)]) is True
    assert records_final([_content()]) is False


def test_a_rejected_record_contributes_no_turns() -> None:
    assert stored_turns([_content(), _content(rejected=True), _content()]) == ['[{"i": 1}]', '[{"i": 1}]']

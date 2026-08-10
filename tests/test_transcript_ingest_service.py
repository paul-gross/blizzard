"""Transcript ingest policy (blizzard#247, Phase 2): lane idempotence, late tails,
ordering, the three independent caps, and truncation recording."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest
from sqlalchemy import text

from blizzard.hub.domain import transcripts as transcripts_domain
from blizzard.hub.domain.transcripts import (
    RECORD_MAX_BYTES,
    REJECTED_CHUNK_BUDGET_EXCEEDED,
    REJECTED_RECORD_TOO_LARGE,
    IWriteTranscriptSegments,
    NaturalKeyState,
    SegmentIndexRow,
    SegmentRecord,
    SegmentRecordContent,
    TranscriptIngestService,
)
from blizzard.hub.store.internal.transcript_segment_store import TranscriptSegmentStore
from tests.support import build_hub, seed_chunk, seed_graph

pytestmark = pytest.mark.component

_T0 = datetime(2026, 8, 9, tzinfo=UTC)


def _seed_chunk(hub, chunk_id: str = "ch_1") -> None:  # type: ignore[no-untyped-def]
    with hub.engine.begin() as conn:
        seed_graph(conn, "gr_1", at=_T0)
        seed_chunk(conn, chunk_id, graph_id="gr_1", at=_T0)


def _record(
    seq: int, *, turn_range_start: int, turn_range_end: int, final: bool = False, **overrides: object
) -> tuple[int, SegmentRecord]:
    values: dict[str, object] = {
        "segment_id": "sg_1",
        "chunk_id": "ch_1",
        "node_id": "nd_build",
        "epoch": 1,
        "spawn_generation": 1,
        "runner_id": "r1",
        "turn_range_start": turn_range_start,
        "turn_range_end": turn_range_end,
        "final": final,
        "normalizer_version": "v1",
        "harness_version": "claude-code-1.0",
        "record_truncated": False,
        "turns_json": f'[{{"index": {turn_range_start}, "kind": "asst", "text": "turn"}}]',
    }
    values.update(overrides)
    return seq, SegmentRecord(**values)  # type: ignore[arg-type]


# --- lane idempotence and ordering -------------------------------------------


def test_replayed_batch_applies_nothing_new_and_returns_the_same_high_water(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    _seed_chunk(hub)
    service = TranscriptIngestService(store=TranscriptSegmentStore(hub.engine), clock=hub.clock)
    batch = [_record(1, turn_range_start=0, turn_range_end=0), _record(2, turn_range_start=1, turn_range_end=1)]

    first = service.ingest("r1", batch)
    assert first.applied == [1, 2]
    assert first.high_water == 2

    replay = service.ingest("r1", batch)
    assert replay.applied == []
    assert replay.already_applied == [1, 2]
    assert replay.high_water == 2


def test_a_batch_straddling_the_mark_applies_only_whats_past_it(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    _seed_chunk(hub)
    service = TranscriptIngestService(store=TranscriptSegmentStore(hub.engine), clock=hub.clock)
    service.ingest("r1", [_record(1, turn_range_start=0, turn_range_end=0)])

    result = service.ingest(
        "r1",
        [
            _record(1, turn_range_start=0, turn_range_end=0),
            _record(2, turn_range_start=1, turn_range_end=1),
        ],
    )
    assert result.already_applied == [1]
    assert result.applied == [2]
    assert result.high_water == 2


def test_a_re_offer_under_a_fresh_seq_dedupes_against_the_natural_key(tmp_path: Path) -> None:
    """D8: a rebuilt buffer or a backfill resends the same ``(segment_id,
    turn_range_start)`` under a *later* lane seq, so the high-water mark cannot catch it
    — the natural key must, and without raising on the schema's unique constraint."""
    hub = build_hub(tmp_path)
    _seed_chunk(hub)
    store = TranscriptSegmentStore(hub.engine)
    service = TranscriptIngestService(store=store, clock=hub.clock)
    service.ingest("r1", [_record(1, turn_range_start=0, turn_range_end=0)])

    result = service.ingest("r1", [_record(7, turn_range_start=0, turn_range_end=0)])

    assert result.applied == [7]
    assert result.high_water == 7
    [content] = store.records_for_segment("ch_1", "sg_1")  # still one row, not two
    assert content.turn_range_start == 0
    assert store.chunk_stored_bytes("ch_1") == len(_record(1, turn_range_start=0, turn_range_end=0)[1].turns_json)


def test_a_below_mark_record_the_hub_no_longer_holds_is_stored_not_reported_idempotent(tmp_path: Path) -> None:
    """The runner PRUNES a row the ack calls ``already_applied``, so reporting idempotency
    for a natural key the hub does not hold destroys the only copy of that content."""
    hub = build_hub(tmp_path)
    _seed_chunk(hub)
    store = TranscriptSegmentStore(hub.engine)
    service = TranscriptIngestService(store=store, clock=hub.clock)
    service.ingest(
        "r1", [_record(1, turn_range_start=0, turn_range_end=0), _record(2, turn_range_start=1, turn_range_end=1)]
    )
    with hub.engine.begin() as conn:
        conn.execute(text("DELETE FROM transcript_segments WHERE turn_range_start = 0"))

    result = service.ingest("r1", [_record(1, turn_range_start=0, turn_range_end=0)])

    assert result.applied == [1]
    assert result.already_applied == []
    assert result.high_water == 2  # still below the mark — the mark itself never moves back
    assert [c.turn_range_start for c in store.records_for_segment("ch_1", "sg_1")] == [0, 1]


def test_a_re_offer_of_a_previously_rejected_record_is_re_adjudicated_not_falsely_applied(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    _seed_chunk(hub)
    store = TranscriptSegmentStore(hub.engine)
    service = TranscriptIngestService(store=store, clock=hub.clock)
    big = "x" * (RECORD_MAX_BYTES + 1)
    service.ingest("r1", [_record(1, turn_range_start=0, turn_range_end=0, turns_json=big)])
    [entry] = store.segments_for_chunk("ch_1")
    assert entry.truncated is True

    result = service.ingest("r1", [_record(99, turn_range_start=0, turn_range_end=0)])

    assert result.applied == [99]
    assert result.capped == []
    [content] = store.records_for_segment("ch_1", "sg_1")
    assert content.rejected is False
    assert content.turns_json == _record(99, turn_range_start=0, turn_range_end=0)[1].turns_json
    [entry] = store.segments_for_chunk("ch_1")
    assert entry.truncated is False


def test_a_re_offer_of_a_still_over_cap_record_stays_capped_not_applied(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    _seed_chunk(hub)
    store = TranscriptSegmentStore(hub.engine)
    service = TranscriptIngestService(store=store, clock=hub.clock)
    big = "x" * (RECORD_MAX_BYTES + 1)
    service.ingest("r1", [_record(1, turn_range_start=0, turn_range_end=0, turns_json=big)])

    result = service.ingest("r1", [_record(2, turn_range_start=0, turn_range_end=0, turns_json=big)])

    assert result.capped == [2]
    assert result.applied == []
    [content] = store.records_for_segment("ch_1", "sg_1")
    assert content.rejected is True
    assert content.turns_json == "[]"


def test_a_tail_record_ingested_after_completion_reads_back_in_turn_range_order(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    _seed_chunk(hub)
    store = TranscriptSegmentStore(hub.engine)
    service = TranscriptIngestService(store=store, clock=hub.clock)

    service.ingest("r1", [_record(1, turn_range_start=0, turn_range_end=0)])
    service.ingest("r1", [_record(2, turn_range_start=1, turn_range_end=1, final=True)])

    records = store.records_for_segment("ch_1", "sg_1")
    assert [r.turn_range_start for r in records] == [0, 1]
    assert records[-1].final is True


def test_a_segment_is_complete_only_on_its_final_marker(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    _seed_chunk(hub)
    store = TranscriptSegmentStore(hub.engine)
    service = TranscriptIngestService(store=store, clock=hub.clock)

    service.ingest("r1", [_record(1, turn_range_start=0, turn_range_end=0, final=False)])
    [entry] = store.segments_for_chunk("ch_1")
    assert entry.final is False

    service.ingest("r1", [_record(2, turn_range_start=1, turn_range_end=1, final=True)])
    [entry] = store.segments_for_chunk("ch_1")
    assert entry.final is True


# --- the three independent caps, against a real migrated store -----------------


def test_an_oversized_record_is_rejected_acked_and_advances_the_high_water(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    _seed_chunk(hub)
    store = TranscriptSegmentStore(hub.engine)
    service = TranscriptIngestService(store=store, clock=hub.clock)
    big = "x" * (RECORD_MAX_BYTES + 1)

    result = service.ingest("r1", [_record(1, turn_range_start=0, turn_range_end=0, turns_json=big)])

    assert result.capped == [1]
    assert result.high_water == 1
    # D6: the advance is *durable*, not just returned — a cap rejection must never be
    # re-adjudicated on replay, so the mark has to survive the call that made it.
    assert store.high_water("r1") == 1
    # A replay must still report the cap outcome — a lost-ack retry (e.g. a runner crash
    # between the hub's apply and its own local ack) must not read as ordinary idempotency.
    replay = service.ingest("r1", [_record(1, turn_range_start=0, turn_range_end=0, turns_json=big)])
    assert replay.already_applied == []
    assert replay.capped == [1]
    [entry] = store.segments_for_chunk("ch_1")
    assert entry.truncated is True


def test_the_chunk_budget_cap_rejects_independently_of_the_other_two(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(transcripts_domain, "CHUNK_BUDGET_MAX_BYTES", 50)
    hub = build_hub(tmp_path)
    _seed_chunk(hub)
    store = TranscriptSegmentStore(hub.engine)
    service = TranscriptIngestService(store=store, clock=hub.clock)
    service.ingest("r1", [_record(1, turn_range_start=0, turn_range_end=0, turns_json="a" * 40)])

    result = service.ingest("r1", [_record(2, turn_range_start=1, turn_range_end=1, turns_json="b" * 40)])

    assert result.capped == [2]
    [entry] = store.segments_for_chunk("ch_1")
    assert entry.truncated is True


def test_the_runner_daily_rate_cap_rejects_independently_of_the_other_two(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(transcripts_domain, "RUNNER_DAILY_RATE_MAX_BYTES", 50)
    hub = build_hub(tmp_path)
    _seed_chunk(hub)
    store = TranscriptSegmentStore(hub.engine)
    service = TranscriptIngestService(store=store, clock=hub.clock)
    service.ingest("r1", [_record(1, turn_range_start=0, turn_range_end=0, turns_json="a" * 40)])

    result = service.ingest(
        "r1", [_record(2, turn_range_start=1, turn_range_end=1, segment_id="sg_2", turns_json="b" * 40)]
    )

    assert result.capped == [2]


def test_a_cap_rejection_leaves_a_readable_truncation_mark_even_as_the_segments_first_record(
    tmp_path: Path,
) -> None:
    hub = build_hub(tmp_path)
    _seed_chunk(hub)
    store = TranscriptSegmentStore(hub.engine)
    service = TranscriptIngestService(store=store, clock=hub.clock)
    big = "x" * (RECORD_MAX_BYTES + 1)

    service.ingest("r1", [_record(1, turn_range_start=0, turn_range_end=0, turns_json=big)])

    [entry] = store.segments_for_chunk("ch_1")
    assert entry.truncated is True
    assert entry.byte_count > 0


# --- byte accounting (unit tier: isolated arithmetic over a fake store) --------


class _FakeTranscriptStore:
    """A minimal :class:`IWriteTranscriptSegments` fake — no persistence, just the
    counters the caps read — isolating Phase 2's byte-accounting rule from the real
    store and its migrations."""

    def __init__(self) -> None:
        self.accepted: list[tuple[SegmentRecord, int]] = []
        self.rejected: list[tuple[SegmentRecord, int, str]] = []
        self.chunk_bytes = 0
        self.runner_bytes = 0

    def segments_for_chunk(self, chunk_id: str) -> list[SegmentIndexRow]:
        raise NotImplementedError

    def records_for_segment(self, chunk_id: str, segment_id: str) -> list[SegmentRecordContent]:
        raise NotImplementedError

    def runner_id_for_lease(self, chunk_id: str, node_id: str, epoch: int) -> str | None:
        raise NotImplementedError

    def records_for_lease(self, chunk_id: str, node_id: str, epoch: int, runner_id: str) -> list[SegmentRecordContent]:
        raise NotImplementedError

    def high_water(self, runner_id: str) -> int:
        return 0

    def set_high_water(self, runner_id: str, *, seq: int, at: datetime) -> None:
        pass

    def natural_key_state(self, segment_id: str, turn_range_start: int) -> NaturalKeyState:
        key = (segment_id, turn_range_start)
        if any((r.segment_id, r.turn_range_start) == key for r, _ in self.accepted):
            return "accepted"
        if any((r.segment_id, r.turn_range_start) == key for r, _, _ in self.rejected):
            return "rejected"
        return "absent"

    def chunk_stored_bytes(self, chunk_id: str) -> int:
        return self.chunk_bytes

    def runner_window_bytes(self, runner_id: str, *, since: datetime) -> int:
        return self.runner_bytes

    def insert_accepted(self, record: SegmentRecord, *, byte_count: int, codec: str, at: datetime) -> None:
        self.accepted.append((record, byte_count))
        self.chunk_bytes += byte_count
        self.runner_bytes += byte_count

    def insert_rejected(self, record: SegmentRecord, *, byte_count: int, reason: str, at: datetime) -> None:
        self.rejected.append((record, byte_count, reason))
        self.runner_bytes += byte_count  # rejected bytes count toward the daily rate only (Phase 2 AC)

    def update_to_accepted(self, record: SegmentRecord, *, byte_count: int, codec: str, at: datetime) -> None:
        key = (record.segment_id, record.turn_range_start)
        self.rejected = [r for r in self.rejected if (r[0].segment_id, r[0].turn_range_start) != key]
        self.accepted.append((record, byte_count))
        self.chunk_bytes += byte_count
        self.runner_bytes += byte_count

    def update_still_rejected(self, record: SegmentRecord, *, byte_count: int, reason: str, at: datetime) -> None:
        key = (record.segment_id, record.turn_range_start)
        self.rejected = [r for r in self.rejected if (r[0].segment_id, r[0].turn_range_start) != key]
        self.rejected.append((record, byte_count, reason))
        self.runner_bytes += byte_count


def _conforms_fake_transcript_store(x: _FakeTranscriptStore) -> IWriteTranscriptSegments:
    return x


class _FixedClock:
    def __init__(self, now: datetime) -> None:
        self._now = now

    def now(self) -> datetime:
        return self._now


def test_stored_bytes_count_toward_both_caps_rejected_bytes_toward_the_daily_rate_only() -> None:
    store = _FakeTranscriptStore()
    service = TranscriptIngestService(store=store, clock=_FixedClock(_T0))

    service.ingest("r1", [_record(1, turn_range_start=0, turn_range_end=0, turns_json="a" * 100)])
    assert store.accepted[0][1] == 100
    assert store.chunk_bytes == 100
    assert store.runner_bytes == 100

    store.chunk_bytes = transcripts_domain.CHUNK_BUDGET_MAX_BYTES
    before_runner_bytes = store.runner_bytes
    service.ingest("r1", [_record(2, turn_range_start=1, turn_range_end=1, turns_json="b" * 100)])

    assert store.rejected and store.rejected[-1][2] == REJECTED_CHUNK_BUDGET_EXCEEDED
    assert store.runner_bytes == before_runner_bytes + 100
    assert store.chunk_bytes == transcripts_domain.CHUNK_BUDGET_MAX_BYTES


def test_the_record_size_cap_is_adjudicated_before_the_others() -> None:
    store = _FakeTranscriptStore()
    service = TranscriptIngestService(store=store, clock=_FixedClock(_T0))
    big = "x" * (RECORD_MAX_BYTES + 1)

    service.ingest("r1", [_record(1, turn_range_start=0, turn_range_end=0, turns_json=big)])

    assert store.rejected[-1][2] == REJECTED_RECORD_TOO_LARGE
    assert not store.accepted

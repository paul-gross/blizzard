"""Transcript segment domain (blizzard#247, ``epic:transcripts``) — the ingest lane and
its store Protocol pair.

:class:`TranscriptIngestService` is the batched store-and-forward push, idempotent
against the lane's own high-water mark (D7) plus the natural-key dedupe (D8), and
adjudicating three independent caps (D5/D6)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Literal, Protocol

from blizzard.foundation.clock import IClock
from blizzard.foundation.logging import get_logger

_log = get_logger("blizzard.hub.transcripts")

#: A natural-key lookup's outcome (D8) — ``"rejected"`` re-adjudicates, never applies outright.
NaturalKeyState = Literal["absent", "accepted", "rejected"]

#: A single record's raw-turn-bytes ceiling — above the epic's measured p99 whole-session
#: size (≈3.3 MB), since one shipped record may carry most of a segment.
RECORD_MAX_BYTES = 4 * 1024 * 1024

#: Per-chunk transcript budget (product plan: "fifty p90 sessions' worth of conversation").
CHUNK_BUDGET_MAX_BYTES = 64 * 1024 * 1024

#: Per-runner rolling-24h rate (product plan: "roughly thirty busy nights' worth in one day").
RUNNER_DAILY_RATE_MAX_BYTES = 2 * 1024 * 1024 * 1024

#: :attr:`SegmentRecord.rejection_reason` values a cap adjudication may set (D5/D6).
REJECTED_RECORD_TOO_LARGE = "record_too_large"
REJECTED_CHUNK_BUDGET_EXCEEDED = "chunk_budget_exceeded"
REJECTED_RUNNER_DAILY_RATE_EXCEEDED = "runner_daily_rate_exceeded"


@dataclass(frozen=True)
class SegmentRecord:
    """One shipped turn-range slice, store-shaped: ``turns_json`` is the record's turns,
    already serialized by the caller (``bzh:domain-core``). ``record_truncated`` is the
    runner's OWN cap declaration, distinct from this hub's own ``rejected`` (below)."""

    segment_id: str
    chunk_id: str
    node_id: str
    epoch: int
    spawn_generation: int
    runner_id: str
    turn_range_start: int
    turn_range_end: int
    final: bool
    normalizer_version: str
    harness_version: str | None
    record_truncated: bool
    turns_json: str


@dataclass(frozen=True)
class SegmentIndexRow:
    """One segment's aggregated metadata (D12) — every stored/rejected record folded into
    its owning segment. ``truncated`` is true iff any record was cap-rejected OR declared
    its own ``record_truncated``, a runner-side loss the hub's own caps never see."""

    segment_id: str
    node_id: str
    epoch: int
    spawn_generation: int
    turn_range_start: int
    turn_range_end: int
    final: bool
    truncated: bool
    byte_count: int
    normalizer_version: str
    harness_version: str | None
    received_at: datetime


@dataclass(frozen=True)
class SegmentRecordContent:
    """One record's decompressed turns, in the order the content route concatenates them.
    ``rejected`` records carry ``turns_json="[]"``. ``record_truncated`` is the runner's
    own declaration that THIS record lost content — ``turns_json`` is often non-empty
    content the runner shrunk, not always ``"[]"``."""

    turn_range_start: int
    turn_range_end: int
    final: bool
    rejected: bool
    record_truncated: bool
    turns_json: str


class IReadTranscriptSegments(Protocol):
    """Read-only operations. The operator-plane index/content routes depend on this
    variant (``bzh:controller-read-only``)."""

    def segments_for_chunk(self, chunk_id: str) -> list[SegmentIndexRow]: ...

    def records_for_segment(self, chunk_id: str, segment_id: str) -> list[SegmentRecordContent]: ...


class IWriteTranscriptSegments(IReadTranscriptSegments, Protocol):
    """Read-write variant. Only :class:`TranscriptIngestService` depends on this."""

    def high_water(self, runner_id: str) -> int: ...

    def set_high_water(self, runner_id: str, *, seq: int, at: datetime) -> None: ...

    def natural_key_state(self, segment_id: str, turn_range_start: int) -> NaturalKeyState: ...

    def chunk_stored_bytes(self, chunk_id: str) -> int: ...

    def runner_window_bytes(self, runner_id: str, *, since: datetime) -> int: ...

    def insert_accepted(self, record: SegmentRecord, *, byte_count: int, codec: str, at: datetime) -> None: ...

    def insert_rejected(self, record: SegmentRecord, *, byte_count: int, reason: str, at: datetime) -> None: ...

    def update_to_accepted(self, record: SegmentRecord, *, byte_count: int, codec: str, at: datetime) -> None: ...

    def update_still_rejected(self, record: SegmentRecord, *, byte_count: int, reason: str, at: datetime) -> None: ...


@dataclass(frozen=True)
class TranscriptIngestResult:
    """:meth:`TranscriptIngestService.ingest`'s own return — the per-seq outcome
    partition an API layer renders into :class:`~blizzard.wire.transcript_segment.TranscriptSegmentAck`."""

    high_water: int
    applied: list[int]
    already_applied: list[int]
    capped: list[int]


class TranscriptIngestService:
    """Apply a runner's batched transcript records idempotently against the transcript
    lane's own high-water mark (D7). Caps are derived by summing stored rows (D2), never
    a maintained counter."""

    def __init__(self, *, store: IWriteTranscriptSegments, clock: IClock) -> None:
        self._store = store
        self._clock = clock

    def ingest(self, runner_id: str, records: list[tuple[int, SegmentRecord]]) -> TranscriptIngestResult:
        """``records`` pairs each record with its lane ``seq`` (the wire batch's own
        per-record field) — kept out of :class:`SegmentRecord` since ``seq`` is a lane
        concept, not part of a record's stored identity."""
        mark = self._store.high_water(runner_id)
        applied: list[int] = []
        already: list[int] = []
        capped: list[int] = []
        now = self._clock.now()

        for seq, record in sorted(records, key=lambda pair: pair[0]):
            if seq <= mark:
                # A replayed already-decided seq must still report its cap outcome: the
                # natural key (D8) is the durable record of that decision, not the ack.
                if self._store.natural_key_state(record.segment_id, record.turn_range_start) == "rejected":
                    capped.append(seq)
                else:
                    already.append(seq)
                continue
            # Every reachable outcome advances the mark (D6) — no contract-mismatch
            # rejection exists here, unlike the fact lane; every field is wire-validated.
            mark = seq
            if self._apply(record, at=now):
                applied.append(seq)
            else:
                capped.append(seq)

        if applied or capped:
            self._store.set_high_water(runner_id, seq=mark, at=now)
        _log.info(
            "transcript segments ingested",
            runner_id=runner_id,
            high_water=mark,
            applied=len(applied),
            already=len(already),
            capped=len(capped),
        )
        return TranscriptIngestResult(high_water=mark, applied=applied, already_applied=already, capped=capped)

    def _apply(self, record: SegmentRecord, *, at: datetime) -> bool:
        """``True`` stored, ``False`` cap-rejected — both advance the high-water (D6)."""
        state = self._store.natural_key_state(record.segment_id, record.turn_range_start)
        if state == "accepted":
            return True

        byte_count = len(record.turns_json.encode("utf-8"))
        reason = self._reject_reason(record, byte_count=byte_count, at=at)
        if state == "rejected":
            if reason is not None:
                self._store.update_still_rejected(record, byte_count=byte_count, reason=reason, at=at)
                return False
            self._store.update_to_accepted(record, byte_count=byte_count, codec="zlib", at=at)
            return True

        if reason is not None:
            self._store.insert_rejected(record, byte_count=byte_count, reason=reason, at=at)
            return False

        self._store.insert_accepted(record, byte_count=byte_count, codec="zlib", at=at)
        return True

    def _reject_reason(self, record: SegmentRecord, *, byte_count: int, at: datetime) -> str | None:
        if byte_count > RECORD_MAX_BYTES:
            return REJECTED_RECORD_TOO_LARGE
        # D4: only already-*stored* bytes count toward the chunk budget — a rejection
        # counts toward the daily rate only (Phase 2 AC).
        if self._store.chunk_stored_bytes(record.chunk_id) + byte_count > CHUNK_BUDGET_MAX_BYTES:
            return REJECTED_CHUNK_BUDGET_EXCEEDED
        since = at - timedelta(hours=24)
        if self._store.runner_window_bytes(record.runner_id, since=since) + byte_count > RUNNER_DAILY_RATE_MAX_BYTES:
            return REJECTED_RUNNER_DAILY_RATE_EXCEEDED
        return None

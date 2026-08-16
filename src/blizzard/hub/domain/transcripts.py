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

#: A single record's raw-turn-bytes ceiling — the rogue-runner backstop, not the working
#: limit: held above `TRANSCRIPT_RECORD_MAX_BYTES`, since over THIS one turns are lost whole.
RECORD_MAX_BYTES = 10 * 1024 * 1024

#: Per-chunk transcript budget (product plan: "fifty p90 sessions' worth of conversation").
CHUNK_BUDGET_MAX_BYTES = 64 * 1024 * 1024

#: Per-runner rolling-24h rate (product plan: "roughly thirty busy nights' worth in one day").
RUNNER_DAILY_RATE_MAX_BYTES = 2 * 1024 * 1024 * 1024

#: :attr:`SegmentRecord.rejection_reason` values a cap adjudication may set (D5/D6).
REJECTED_RECORD_TOO_LARGE = "record_too_large"
REJECTED_CHUNK_BUDGET_EXCEEDED = "chunk_budget_exceeded"
REJECTED_RUNNER_DAILY_RATE_EXCEEDED = "runner_daily_rate_exceeded"


@dataclass(frozen=True)
class TranscriptCaps:
    """The three ceilings :meth:`TranscriptIngestService._reject_reason` adjudicates, resolved
    from configuration rather than read as constants — an operator widens them for a backfill
    window (a re-ship spends the per-chunk budget a second time) and restores them after. The
    defaults ARE the module constants above, so an unconfigured hub is unchanged."""

    record_max_bytes: int = RECORD_MAX_BYTES
    chunk_budget_max_bytes: int = CHUNK_BUDGET_MAX_BYTES
    runner_daily_rate_max_bytes: int = RUNNER_DAILY_RATE_MAX_BYTES


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
    #: Re-ship only: the segment this replaces, which a lease read drops (blizzard#250).
    supersedes: str | None = None


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

    def runner_id_for_lease(self, chunk_id: str, node_id: str, epoch: int) -> str | None:
        """The ``runner_id`` on a lease's stored segments (D2), or ``None`` when it holds
        none — the fleet-plane read route's own ownership signal (issue #249), resolved
        independently of the caller so the route can refuse a mismatch."""
        ...

    def records_for_lease(self, chunk_id: str, node_id: str, epoch: int, runner_id: str) -> list[SegmentRecordContent]:
        """Every accepted-or-rejected record across a lease's ``(chunk_id, node_id, epoch)``
        (D2), across every spawn generation, confined to ``runner_id``."""
        ...


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

    def __init__(self, *, store: IWriteTranscriptSegments, clock: IClock, caps: TranscriptCaps | None = None) -> None:
        self._store = store
        self._clock = clock
        self._caps = caps if caps is not None else TranscriptCaps()

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
                state = self._store.natural_key_state(record.segment_id, record.turn_range_start)
                if state == "rejected":
                    capped.append(seq)
                    continue
                if state == "accepted":
                    already.append(seq)
                    continue
                # Under the mark with no row: the mark outran the store. Reporting idempotency
                # would have the runner ack and delete the only copy, so store it instead.
                _log.warning(
                    "transcript high-water is ahead of the stored record — applying it anyway",
                    runner_id=runner_id,
                    seq=seq,
                    segment_id=record.segment_id,
                )
                if self._apply(record, at=now):
                    applied.append(seq)
                else:
                    capped.append(seq)
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
        if byte_count > self._caps.record_max_bytes:
            return self._rejected(record, REJECTED_RECORD_TOO_LARGE, byte_count, self._caps.record_max_bytes)
        # D4: only already-*stored* bytes count toward the chunk budget — a rejection
        # counts toward the daily rate only (Phase 2 AC).
        stored = self._store.chunk_stored_bytes(record.chunk_id)
        if stored + byte_count > self._caps.chunk_budget_max_bytes:
            return self._rejected(
                record, REJECTED_CHUNK_BUDGET_EXCEEDED, stored + byte_count, self._caps.chunk_budget_max_bytes
            )
        since = at - timedelta(hours=24)
        window = self._store.runner_window_bytes(record.runner_id, since=since)
        if window + byte_count > self._caps.runner_daily_rate_max_bytes:
            return self._rejected(
                record,
                REJECTED_RUNNER_DAILY_RATE_EXCEEDED,
                window + byte_count,
                self._caps.runner_daily_rate_max_bytes,
            )
        return None

    @staticmethod
    def _rejected(record: SegmentRecord, reason: str, observed: int, limit: int) -> str:
        """Log the CONFIGURED limit alongside the reason: a rejection reads as a bug when the
        operator cannot tell which ceiling bound it, and the ceilings are no longer constants
        an operator could look up. Returns ``reason`` so the caller stays a single expression."""
        _log.warning(
            "transcript record rejected",
            segment_id=record.segment_id,
            chunk_id=record.chunk_id,
            runner_id=record.runner_id,
            turn_range_start=record.turn_range_start,
            reason=reason,
            observed_bytes=observed,
            limit_bytes=limit,
        )
        return reason

"""Transcript segment domain (``epic:transcripts``) — the ingest lane and
its store Protocol pair.

:class:`TranscriptIngestService` is the batched store-and-forward push, idempotent
against the lane's own high-water mark plus the natural-key dedupe, and
adjudicating three independent caps."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import StrEnum
from typing import Literal, Protocol

from blizzard.foundation.clock import IClock
from blizzard.foundation.logging import get_logger
from blizzard.foundation.roles import domain_model, dto
from blizzard.hub.domain.chunk.ports.fence import EpochOwner
from blizzard.hub.domain.runners.registration import RetiredRunnerGuard

_log = get_logger("blizzard.hub.transcripts")

#: A natural-key lookup's outcome — ``"rejected"`` re-adjudicates, never applies outright.
NaturalKeyState = Literal["absent", "accepted", "rejected"]

#: A single record's raw-turn-bytes ceiling — the rogue-runner backstop, not the working
#: limit: held above `TRANSCRIPT_RECORD_MAX_BYTES`, since over THIS one turns are lost whole.
RECORD_MAX_BYTES = 10 * 1024 * 1024

#: Per-chunk transcript budget (product plan: "fifty p90 sessions' worth of conversation").
CHUNK_BUDGET_MAX_BYTES = 64 * 1024 * 1024

#: Per-runner rolling-24h rate (product plan: "roughly thirty busy nights' worth in one day").
RUNNER_DAILY_RATE_MAX_BYTES = 2 * 1024 * 1024 * 1024

#: :attr:`TranscriptSlice.rejection_reason` values a cap adjudication may set.
REJECTED_RECORD_TOO_LARGE = "record_too_large"
REJECTED_CHUNK_BUDGET_EXCEEDED = "chunk_budget_exceeded"
REJECTED_RUNNER_DAILY_RATE_EXCEEDED = "runner_daily_rate_exceeded"


#: The window the per-runner rate cap sums over.
RUNNER_RATE_WINDOW = timedelta(hours=24)


@domain_model
@dataclass(frozen=True)
class CapBreach:
    """One cap a record breaks: the reject reason, the bytes it would bring the cap to, and the cap."""

    reason: str
    observed: int
    limit: int


@domain_model
@dataclass(frozen=True)
class TranscriptCaps:
    """The three ceilings a record is adjudicated against, resolved
    from configuration rather than read as constants — an operator widens them for a backfill
    window (a re-ship spends the per-chunk budget a second time) and restores them after. The
    defaults ARE the module constants above, so an unconfigured hub is unchanged."""

    record_max_bytes: int = RECORD_MAX_BYTES
    chunk_budget_max_bytes: int = CHUNK_BUDGET_MAX_BYTES
    runner_daily_rate_max_bytes: int = RUNNER_DAILY_RATE_MAX_BYTES

    # The ladder is three steps, first breach wins: record ceiling, per-chunk budget, per-runner rate;
    # each step takes only the sum it needs, read only once every earlier step has passed.

    def record_breach(self, byte_count: int) -> CapBreach | None:
        if byte_count > self.record_max_bytes:
            return CapBreach(REJECTED_RECORD_TOO_LARGE, byte_count, self.record_max_bytes)
        return None

    def chunk_breach(self, byte_count: int, *, chunk_stored: int) -> CapBreach | None:
        """Only already-*stored* bytes count toward the chunk budget — a rejection counts toward
        the runner's rate only, never the chunk budget."""
        if chunk_stored + byte_count > self.chunk_budget_max_bytes:
            return CapBreach(REJECTED_CHUNK_BUDGET_EXCEEDED, chunk_stored + byte_count, self.chunk_budget_max_bytes)
        return None

    def runner_breach(self, byte_count: int, *, runner_window: int) -> CapBreach | None:
        if runner_window + byte_count > self.runner_daily_rate_max_bytes:
            return CapBreach(
                REJECTED_RUNNER_DAILY_RATE_EXCEEDED, runner_window + byte_count, self.runner_daily_rate_max_bytes
            )
        return None

    @staticmethod
    def window_start(at: datetime) -> datetime:
        """Where the rolling per-runner rate window opens for a record arriving ``at``."""
        return at - RUNNER_RATE_WINDOW


@dto
@dataclass(frozen=True)
class TranscriptSlice:
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
    #: Re-ship only: the segment this replaces, which a lease read drops.
    supersedes: str | None = None
    harness_id: str | None = None
    #: Frozen at the runner's segment open.
    model: str | None = None
    effort: str | None = None
    #: The worker's working directory, frozen at the runner's segment open.
    spawn_cwd: str | None = None

    @property
    def byte_count(self) -> int:
        """The record's size as every cap counts it: its turns' UTF-8 bytes."""
        return len(self.turns_json.encode("utf-8"))


@dto
@dataclass(frozen=True)
class SegmentSummary:
    """One segment's aggregated metadata — every stored/rejected record folded into
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
    harness_id: str | None = None


@dto
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

    def segments_for_chunk(self, chunk_id: str) -> list[SegmentSummary]: ...

    def records_for_segment(self, chunk_id: str, segment_id: str) -> list[SegmentRecordContent]: ...

    def runner_id_for_lease(self, chunk_id: str, node_id: str, epoch: int) -> str | None:
        """The ``runner_id`` on a lease's stored segments, or ``None`` when it holds
        none; resolved from stored segments, independent of any caller-supplied runner."""
        ...

    def records_for_lease(self, chunk_id: str, node_id: str, epoch: int, runner_id: str) -> list[SegmentRecordContent]:
        """Every accepted-or-rejected record across a lease's ``(chunk_id, node_id, epoch)``,
        across every spawn generation, confined to ``runner_id``."""
        ...


class IWriteTranscriptSegments(IReadTranscriptSegments, Protocol):
    """Read-write variant. Only :class:`TranscriptIngestService` depends on this."""

    def high_water(self, runner_id: str) -> int: ...

    def set_high_water(self, runner_id: str, *, seq: int, at: datetime) -> None: ...

    def natural_key_state(self, segment_id: str, turn_range_start: int) -> NaturalKeyState: ...

    def epoch_owner(self, chunk_id: str, epoch: int) -> EpochOwner | None:
        """The owner recorded for the record's lease epoch, or ``None`` while none is recorded."""
        ...

    def chunk_stored_bytes(self, chunk_id: str) -> int: ...

    def runner_window_bytes(self, runner_id: str, *, since: datetime) -> int: ...

    def insert_accepted(self, record: TranscriptSlice, *, byte_count: int, codec: str, at: datetime) -> None: ...

    def insert_rejected(self, record: TranscriptSlice, *, byte_count: int, reason: str, at: datetime) -> None: ...

    def update_to_accepted(self, record: TranscriptSlice, *, byte_count: int, codec: str, at: datetime) -> None: ...

    def update_still_rejected(self, record: TranscriptSlice, *, byte_count: int, reason: str, at: datetime) -> None: ...


class SegmentWrite(StrEnum):
    """What one record's natural-key state and cap verdict write. First write wins: an
    accepted key is never rewritten, even by a re-ship carrying different content (a changed
    re-ship opens a superseding segment instead). A rejected key is re-adjudicated."""

    KEEP = "keep"
    INSERT_ACCEPTED = "insert_accepted"
    INSERT_REJECTED = "insert_rejected"
    UPDATE_TO_ACCEPTED = "update_to_accepted"
    UPDATE_STILL_REJECTED = "update_still_rejected"

    @property
    def stored(self) -> bool:
        """Whether the record ends stored (``True``) or cap-rejected (``False``)."""
        return self._ends_stored()

    def _ends_stored(self) -> bool:
        return self in (SegmentWrite.KEEP, SegmentWrite.INSERT_ACCEPTED, SegmentWrite.UPDATE_TO_ACCEPTED)


class LeaseSegmentsNotOwned(Exception):
    """A runner asked to read back a lease's stored segments another runner shipped."""

    def __init__(self, *, owning_runner_id: str, requesting_runner_id: str) -> None:
        super().__init__("lease segments belong to another runner")
        self.owning_runner_id = owning_runner_id
        self.requesting_runner_id = requesting_runner_id


def refuse_foreign_lease_read(owning_runner_id: str | None, *, requesting_runner_id: str) -> None:
    """Refuse a runner's read-back of a lease's segments when another runner shipped them
    (:class:`LeaseSegmentsNotOwned`). Keyed on stored-segment authorship on purpose — the read
    answers whose stored segments these are, which ingest's :func:`ships_from_lease_holder` keeps
    equal to the epoch's holder. ``None`` (the hub holds nothing) is no refusal."""
    if owning_runner_id is not None and owning_runner_id != requesting_runner_id:
        raise LeaseSegmentsNotOwned(owning_runner_id=owning_runner_id, requesting_runner_id=requesting_runner_id)


def ships_from_lease_holder(owner: EpochOwner | None, runner_id: str) -> bool:
    """Whether a record shipped by ``runner_id`` comes from its lease's holder. An epoch owned by
    the hub or by another runner refuses it; an epoch with no owner recorded yet admits it, since
    the fact lane that records the owner may trail the transcript lane."""
    return owner is None or owner.runner_id == runner_id


def adjudicates(state: NaturalKeyState) -> bool:
    """Whether a record at ``state`` goes through the cap ladder — every state but accepted."""
    return state != "accepted"


def segment_write(state: NaturalKeyState, reject_reason: str | None) -> SegmentWrite:
    """The write for a record at ``state`` whose cap ladder returned ``reject_reason``."""
    if state == "accepted":
        return SegmentWrite.KEEP
    if state == "rejected":
        return SegmentWrite.UPDATE_TO_ACCEPTED if reject_reason is None else SegmentWrite.UPDATE_STILL_REJECTED
    return SegmentWrite.INSERT_ACCEPTED if reject_reason is None else SegmentWrite.INSERT_REJECTED


ReplayOutcome = Literal["capped", "already_applied", "apply"]


def replay_outcome(state: NaturalKeyState) -> ReplayOutcome:
    """A seq at or under the runner's high-water mark: its natural key is the durable record of
    the decision, so a rejected key reports ``capped`` without re-adjudication and an accepted
    one ``already_applied``. An absent key means the mark outran the store: reporting it
    idempotent would have the runner ack and delete the only copy, so it is applied."""
    if state == "rejected":
        return "capped"
    if state == "accepted":
        return "already_applied"
    return "apply"


def lost_turns(*, rejected: bool, record_truncated: bool | None) -> bool:
    """Whether one record lost turns: this hub cap-rejected it, or the runner declared it
    truncated (an unset declaration reads as not truncated)."""
    return rejected or bool(record_truncated)


def records_truncated(records: Iterable[SegmentRecordContent]) -> bool:
    """A segment or lease is truncated iff any of its records lost turns."""
    return any(lost_turns(rejected=r.rejected, record_truncated=r.record_truncated) for r in records)


def records_final(records: Iterable[SegmentRecordContent]) -> bool:
    """A segment is final iff any of its records closed it out."""
    return any(r.final for r in records)


def stored_turns(records: Iterable[SegmentRecordContent]) -> list[str]:
    """Each stored record's serialized turns in record order; a rejected record contributes none."""
    return [r.turns_json for r in records if not r.rejected]


@dto
@dataclass(frozen=True)
class TranscriptIngestResult:
    """:meth:`TranscriptIngestService.ingest`'s own return — the per-seq outcome
    partition an API layer renders into :class:`~blizzard.wire.transcript_segment.TranscriptSegmentAck`."""

    high_water: int
    applied: list[int]
    already_applied: list[int]
    capped: list[int]
    refused: list[int]


class TranscriptIngestService:
    """Apply a runner's batched transcript records idempotently against the transcript
    lane's own high-water mark. Caps are derived by summing stored rows, never
    a maintained counter."""

    def __init__(
        self,
        *,
        store: IWriteTranscriptSegments,
        retired: RetiredRunnerGuard,
        clock: IClock,
        caps: TranscriptCaps | None = None,
    ) -> None:
        self._store = store
        self._retired = retired
        self._clock = clock
        self._caps = caps if caps is not None else TranscriptCaps()

    def ingest(self, runner_id: str, records: list[tuple[int, TranscriptSlice]]) -> TranscriptIngestResult:
        """``records`` pairs each record with its lane ``seq``, kept out of :class:`TranscriptSlice`
        since ``seq`` is a lane concept, not a record's stored identity. A retired runner is refused with
        :class:`RunnerRetired` before anything lands. A record whose lease another holder owns
        (:func:`ships_from_lease_holder`) is refused unstored, on replay too, and the mark advances past it."""
        self._retired.refuse_if_retired(runner_id, action="transcript ingest")
        mark = self._store.high_water(runner_id)
        applied: list[int] = []
        already: list[int] = []
        capped: list[int] = []
        refused: list[int] = []
        now = self._clock.now()
        owners: dict[tuple[str, int], EpochOwner | None] = {}

        for seq, record in sorted(records, key=lambda pair: pair[0]):
            lease = (record.chunk_id, record.epoch)
            if lease not in owners:
                owners[lease] = self._store.epoch_owner(*lease)
            if not ships_from_lease_holder(owners[lease], runner_id):
                refused.append(seq)
                mark = max(mark, seq)
                continue
            if seq <= mark:
                outcome = replay_outcome(self._store.natural_key_state(record.segment_id, record.turn_range_start))
                if outcome == "capped":
                    capped.append(seq)
                    continue
                if outcome == "already_applied":
                    already.append(seq)
                    continue
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
            # Every reachable outcome advances the mark — no contract-mismatch
            # rejection exists here, unlike the fact lane; every field is wire-validated.
            mark = seq
            if self._apply(record, at=now):
                applied.append(seq)
            else:
                capped.append(seq)

        if applied or capped or refused:
            self._store.set_high_water(runner_id, seq=mark, at=now)
        _log.info(
            "transcript segments ingested",
            runner_id=runner_id,
            high_water=mark,
            applied=len(applied),
            already=len(already),
            capped=len(capped),
            refused=len(refused),
        )
        return TranscriptIngestResult(
            high_water=mark, applied=applied, already_applied=already, capped=capped, refused=refused
        )

    def _apply(self, record: TranscriptSlice, *, at: datetime) -> bool:
        """``True`` stored, ``False`` cap-rejected — both advance the high-water."""
        state = self._store.natural_key_state(record.segment_id, record.turn_range_start)
        byte_count = record.byte_count
        reason = self._reject_reason(record, byte_count=byte_count, at=at) if adjudicates(state) else None
        write = segment_write(state, reason)
        if write is SegmentWrite.UPDATE_STILL_REJECTED and reason is not None:
            self._store.update_still_rejected(record, byte_count=byte_count, reason=reason, at=at)
        elif write is SegmentWrite.UPDATE_TO_ACCEPTED:
            self._store.update_to_accepted(record, byte_count=byte_count, codec="zlib", at=at)
        elif write is SegmentWrite.INSERT_REJECTED and reason is not None:
            self._store.insert_rejected(record, byte_count=byte_count, reason=reason, at=at)
        elif write is SegmentWrite.INSERT_ACCEPTED:
            self._store.insert_accepted(record, byte_count=byte_count, codec="zlib", at=at)
        return write.stored

    def _reject_reason(self, record: TranscriptSlice, *, byte_count: int, at: datetime) -> str | None:
        """Walk the cap ladder, reading each stored sum only once the earlier steps pass."""
        caps = self._caps
        breach = caps.record_breach(byte_count)
        if breach is None:
            breach = caps.chunk_breach(byte_count, chunk_stored=self._store.chunk_stored_bytes(record.chunk_id))
        if breach is None:
            window = self._store.runner_window_bytes(record.runner_id, since=caps.window_start(at))
            breach = caps.runner_breach(byte_count, runner_window=window)
        return None if breach is None else self._rejected(record, breach)

    @staticmethod
    def _rejected(record: TranscriptSlice, breach: CapBreach) -> str:
        """Log the CONFIGURED limit alongside the reason: a rejection reads as a bug when the
        operator cannot tell which ceiling bound it, and the ceilings are no longer constants
        an operator could look up. Returns ``reason`` so the caller stays a single expression."""
        _log.warning(
            "transcript record rejected",
            segment_id=record.segment_id,
            chunk_id=record.chunk_id,
            runner_id=record.runner_id,
            turn_range_start=record.turn_range_start,
            reason=breach.reason,
            observed_bytes=breach.observed,
            limit_bytes=breach.limit,
        )
        return breach.reason

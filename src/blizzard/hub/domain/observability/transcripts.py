"""Transcript segment domain (``epic:transcripts``) — the ingest lane and
its store Protocol pair.

:class:`TranscriptIngestService` is the batched store-and-forward push, idempotent
against the lane's own high-water mark plus the natural-key dedupe, and
adjudicating three independent caps, each batch decided inside one write unit."""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import StrEnum
from typing import Any, Literal, Protocol

from blizzard.foundation.clock import IClock
from blizzard.foundation.logging import get_logger
from blizzard.foundation.roles import domain_model
from blizzard.foundation.transcripts import TurnKind
from blizzard.hub.domain.chunk.ports.fence import EpochOwner
from blizzard.hub.domain.kernel.transactions import IHubTransactions, LockedTransaction, Transaction
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

#: The keyed-lock namespace (``bzh:transaction-graduation``) of a runner's transcript lane — its
#: high-water mark and its rolling rate window.
TRANSCRIPT_LANE_LOCK_NAMESPACE = "transcript_lane"

#: The keyed-lock namespace of one chunk's transcript — its budget and the natural keys of its records.
TRANSCRIPT_CHUNK_LOCK_NAMESPACE = "transcript_chunk"


def transcript_lane_lock(runner_id: str) -> tuple[str, str]:
    """The keyed lock over ``runner_id``'s transcript lane."""
    return (TRANSCRIPT_LANE_LOCK_NAMESPACE, runner_id)


def transcript_chunk_lock(chunk_id: str) -> tuple[str, str]:
    """The keyed lock over ``chunk_id``'s transcript."""
    return (TRANSCRIPT_CHUNK_LOCK_NAMESPACE, chunk_id)


def ingest_locks(runner_id: str, records: Iterable[TranscriptSlice]) -> list[tuple[str, str]]:
    """Every lock one ingest declares: the calling runner's lane and each chunk ``records`` name."""
    chunk_ids = sorted({record.chunk_id for record in records})
    return [transcript_lane_lock(runner_id), *(transcript_chunk_lock(chunk_id) for chunk_id in chunk_ids)]


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
    """The three ceilings a record is adjudicated against, resolved from configuration; the
    defaults are the module constants above."""

    record_max_bytes: int = RECORD_MAX_BYTES
    chunk_budget_max_bytes: int = CHUNK_BUDGET_MAX_BYTES
    runner_daily_rate_max_bytes: int = RUNNER_DAILY_RATE_MAX_BYTES

    def record_breach(self, byte_count: int) -> CapBreach | None:
        if byte_count > self.record_max_bytes:
            return CapBreach(REJECTED_RECORD_TOO_LARGE, byte_count, self.record_max_bytes)
        return None

    def chunk_breach(self, byte_count: int, *, chunk_stored: int) -> CapBreach | None:
        """``chunk_stored`` is the chunk's already-*stored* bytes, the only bytes counted toward its budget."""
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


@domain_model
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


@domain_model
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


@domain_model
@dataclass(frozen=True)
class SegmentRecordContent:
    """One record's decompressed turns, in record order.
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
    """Read-only operations."""

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


class EncodedTurns(Protocol):
    """A record's turns encoded as the store keeps them (:meth:`IWriteTranscriptSegments.encode_turns`) —
    opaque here, and prepared before a unit opens so no write holds its locks for the encoding."""


class IWriteTranscriptSegments(IReadTranscriptSegments, Protocol):
    """Adds the ingest's guard reads and its writes. Each read joins a supplied unit or runs alone;
    each write is correct only in the unit that decided it, which locks the record's chunk
    (:func:`transcript_chunk_lock`) and its runner's lane (:func:`transcript_lane_lock`)."""

    def high_water(self, runner_id: str, *, transaction: Transaction | None = None) -> int: ...

    def set_high_water(self, runner_id: str, *, seq: int, at: datetime, transaction: LockedTransaction) -> None:
        """Write ``seq`` as the mark, as given: the caller decided it never moves the mark back."""
        ...

    def natural_key_state(
        self, segment_id: str, turn_range_start: int, *, transaction: Transaction | None = None
    ) -> NaturalKeyState: ...

    def epoch_owner(self, chunk_id: str, epoch: int) -> EpochOwner | None:
        """The owner recorded for the record's lease epoch, or ``None`` while none is recorded."""
        ...

    def epoch_owners(
        self, leases: Sequence[tuple[str, int]], *, transaction: Transaction | None = None
    ) -> dict[tuple[str, int], EpochOwner]:
        """`epoch_owner`'s batched sibling (`bzh:bulk-reconstitution`), keyed by ``(chunk_id, epoch)``
        — an epoch with no recorded owner is dropped."""
        ...

    def chunk_stored_bytes(self, chunk_id: str, *, transaction: Transaction | None = None) -> int: ...

    def runner_window_bytes(
        self, runner_id: str, *, since: datetime, transaction: Transaction | None = None
    ) -> int: ...

    def encode_turns(self, record: TranscriptSlice) -> EncodedTurns:
        """``record``'s turns as an accepted write stores them; runs on no connection."""
        ...

    def insert_accepted(
        self,
        record: TranscriptSlice,
        *,
        turns: EncodedTurns,
        byte_count: int,
        at: datetime,
        transaction: LockedTransaction,
    ) -> None: ...

    def insert_rejected(
        self, record: TranscriptSlice, *, byte_count: int, reason: str, at: datetime, transaction: LockedTransaction
    ) -> None: ...

    def update_to_accepted(
        self,
        record: TranscriptSlice,
        *,
        turns: EncodedTurns,
        byte_count: int,
        at: datetime,
        transaction: LockedTransaction,
    ) -> None: ...

    def update_still_rejected(
        self, record: TranscriptSlice, *, byte_count: int, reason: str, at: datetime, transaction: LockedTransaction
    ) -> None: ...


class SegmentWrite(StrEnum):
    """What one record's natural-key state and cap verdict write. First write wins: an
    accepted key is never rewritten, even by a re-ship carrying different content. A rejected key is re-adjudicated."""

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
    (:class:`LeaseSegmentsNotOwned`). ``None`` (the hub holds nothing) is no refusal."""
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


@domain_model
@dataclass(frozen=True)
class TranscriptIngestResult:
    """:meth:`TranscriptIngestService.ingest`'s own return — the per-seq outcome partition."""

    high_water: int
    applied: list[int]
    already_applied: list[int]
    capped: list[int]
    refused: list[int]


class TranscriptIngestService:
    """Apply a runner's batched transcript records idempotently against the transcript
    lane's own high-water mark. Caps are derived by summing stored rows, never
    a maintained counter.

    Every guard a record's decision reads is a sum or a key no one version covers, so each batch
    is decided inside one unit (``bzh:write-reestablishes-read``): graduated once on
    :func:`ingest_locks`, every guard re-read and every write made with its token."""

    def __init__(
        self,
        *,
        store: IWriteTranscriptSegments,
        transactions: IHubTransactions,
        retired: RetiredRunnerGuard,
        clock: IClock,
        caps: TranscriptCaps | None = None,
    ) -> None:
        self._store = store
        self._transactions = transactions
        self._retired = retired
        self._clock = clock
        self._caps = caps if caps is not None else TranscriptCaps()

    def ingest(self, runner_id: str, records: list[tuple[int, TranscriptSlice]]) -> TranscriptIngestResult:
        """``records`` pairs each record with its lane ``seq``, kept out of :class:`TranscriptSlice`
        since ``seq`` is a lane concept, not a record's stored identity. A retired runner is refused with
        :class:`RunnerRetired` before anything lands. A record whose lease another holder owns
        (:func:`ships_from_lease_holder`) is refused unstored, on replay too, and the mark advances past it.
        The batch and its mark commit together or not at all, and are logged only once committed."""
        self._retired.refuse_if_retired(runner_id, action="transcript ingest")
        now = self._clock.now()
        breaches: list[tuple[TranscriptSlice, CapBreach]] = []
        ahead: list[tuple[int, TranscriptSlice]] = []
        prepared = self._prepare(records)
        with self._transactions.begin() as transaction:
            locked = transaction.lock_set(keys=ingest_locks(runner_id, [record for _, record, _ in prepared]))
            result = self._decide(runner_id, prepared, at=now, transaction=locked, breaches=breaches, ahead=ahead)
        for seq, record in ahead:
            _log.warning(
                "transcript high-water is ahead of the stored record — applying it anyway",
                runner_id=runner_id,
                seq=seq,
                segment_id=record.segment_id,
            )
        for record, breach in breaches:
            self._log_rejection(record, breach)
        _log.info(
            "transcript segments ingested",
            runner_id=runner_id,
            high_water=result.high_water,
            applied=len(result.applied),
            already=len(result.already_applied),
            capped=len(result.capped),
            refused=len(result.refused),
        )
        return result

    def _prepare(
        self, records: list[tuple[int, TranscriptSlice]]
    ) -> list[tuple[int, TranscriptSlice, EncodedTurns | None]]:
        """``records`` in seq order, each beside its encoded turns — ``None`` for a record over the
        record cap, which no decision stores."""
        return [
            (
                seq,
                record,
                self._store.encode_turns(record) if self._caps.record_breach(record.byte_count) is None else None,
            )
            for seq, record in sorted(records, key=lambda pair: pair[0])
        ]

    def _decide(
        self,
        runner_id: str,
        records: list[tuple[int, TranscriptSlice, EncodedTurns | None]],
        *,
        at: datetime,
        transaction: LockedTransaction,
        breaches: list[tuple[TranscriptSlice, CapBreach]],
        ahead: list[tuple[int, TranscriptSlice]],
    ) -> TranscriptIngestResult:
        """Decide and write every record and the mark in ``transaction``, from guards read in it.
        The mark written is the larger of the one read here and the batch's highest decided seq."""
        mark = self._store.high_water(runner_id, transaction=transaction)
        applied: list[int] = []
        already: list[int] = []
        capped: list[int] = []
        refused: list[int] = []
        owners = self._store.epoch_owners(
            [(record.chunk_id, record.epoch) for _, record, _ in records], transaction=transaction
        )

        for seq, record, turns in records:
            if not ships_from_lease_holder(owners.get((record.chunk_id, record.epoch)), runner_id):
                refused.append(seq)
                mark = max(mark, seq)
                continue
            if seq <= mark:
                state = self._store.natural_key_state(
                    record.segment_id, record.turn_range_start, transaction=transaction
                )
                outcome = replay_outcome(state)
                if outcome == "capped":
                    capped.append(seq)
                    continue
                if outcome == "already_applied":
                    already.append(seq)
                    continue
                ahead.append((seq, record))
                if self._apply(record, turns, at=at, transaction=transaction, breaches=breaches):
                    applied.append(seq)
                else:
                    capped.append(seq)
                continue
            # Every reachable outcome advances the mark; every field is wire-validated.
            mark = seq
            if self._apply(record, turns, at=at, transaction=transaction, breaches=breaches):
                applied.append(seq)
            else:
                capped.append(seq)

        if applied or capped or refused:
            self._store.set_high_water(runner_id, seq=mark, at=at, transaction=transaction)
        return TranscriptIngestResult(
            high_water=mark, applied=applied, already_applied=already, capped=capped, refused=refused
        )

    def _apply(
        self,
        record: TranscriptSlice,
        turns: EncodedTurns | None,
        *,
        at: datetime,
        transaction: LockedTransaction,
        breaches: list[tuple[TranscriptSlice, CapBreach]],
    ) -> bool:
        """``True`` stored, ``False`` cap-rejected — both advance the high-water. A breach the
        ladder finds is appended to ``breaches``; ``turns`` is ``None`` only for a record the
        record cap rejects."""
        store = self._store
        state = store.natural_key_state(record.segment_id, record.turn_range_start, transaction=transaction)
        byte_count = record.byte_count
        breach = (
            self._breach(record, byte_count=byte_count, at=at, transaction=transaction) if adjudicates(state) else None
        )
        if breach is not None:
            breaches.append((record, breach))
        reason = None if breach is None else breach.reason
        write = segment_write(state, reason)
        if write is SegmentWrite.UPDATE_STILL_REJECTED and reason is not None:
            store.update_still_rejected(record, byte_count=byte_count, reason=reason, at=at, transaction=transaction)
        elif write is SegmentWrite.UPDATE_TO_ACCEPTED and turns is not None:
            store.update_to_accepted(record, turns=turns, byte_count=byte_count, at=at, transaction=transaction)
        elif write is SegmentWrite.INSERT_REJECTED and reason is not None:
            store.insert_rejected(record, byte_count=byte_count, reason=reason, at=at, transaction=transaction)
        elif write is SegmentWrite.INSERT_ACCEPTED and turns is not None:
            store.insert_accepted(record, turns=turns, byte_count=byte_count, at=at, transaction=transaction)
        return write.stored

    def _breach(
        self, record: TranscriptSlice, *, byte_count: int, at: datetime, transaction: LockedTransaction
    ) -> CapBreach | None:
        """Walk the cap ladder, reading each stored sum only once the earlier steps pass."""
        caps = self._caps
        breach = caps.record_breach(byte_count)
        if breach is None:
            stored = self._store.chunk_stored_bytes(record.chunk_id, transaction=transaction)
            breach = caps.chunk_breach(byte_count, chunk_stored=stored)
        if breach is None:
            window = self._store.runner_window_bytes(
                record.runner_id, since=caps.window_start(at), transaction=transaction
            )
            breach = caps.runner_breach(byte_count, runner_window=window)
        return breach

    @staticmethod
    def _log_rejection(record: TranscriptSlice, breach: CapBreach) -> None:
        """Log the CONFIGURED limit alongside the reason: a rejection reads as a bug when the
        operator cannot tell which ceiling bound it, and the ceilings are configured, not
        constants."""
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


@domain_model
@dataclass(frozen=True)
class TurnToolCall:
    """A tool invocation inside a stored turn: what was called, with what input, and what came
    back. ``output_patch`` marks a turn carrying only a result for an earlier window's call."""

    name: str
    input: dict[str, object]
    input_unparsed: str | None
    input_shape: str
    tool_use_id: str | None
    output: str | None
    output_truncated: bool
    input_truncated: bool = False
    output_patch: bool = False

    @classmethod
    def of_stored(cls, stored: dict[str, Any]) -> TurnToolCall:
        return cls(
            name=stored["name"],
            input=dict(stored["input"]),
            input_unparsed=stored["input_unparsed"],
            input_shape=stored["input_shape"],
            tool_use_id=stored["tool_use_id"],
            output=stored["output"],
            output_truncated=stored["output_truncated"],
            input_truncated=stored.get("input_truncated", False),
            output_patch=stored.get("output_patch", False),
        )


@domain_model
@dataclass(frozen=True)
class TurnSidechain:
    """A subagent's private conversation, nested under the call that spawned it — its own
    turns, recursively."""

    agent_id: str | None
    agent_type: str | None
    link: str
    turns: list[TranscriptTurn]
    parent_tool_use_id: str | None = None

    @classmethod
    def of_stored(cls, stored: dict[str, Any]) -> TurnSidechain:
        return cls(
            agent_id=stored["agent_id"],
            agent_type=stored["agent_type"],
            link=stored["link"],
            turns=[TranscriptTurn.of_stored(turn) for turn in stored["turns"]],
            parent_tool_use_id=stored.get("parent_tool_use_id"),
        )


@domain_model
@dataclass(frozen=True)
class TranscriptTurn:
    """One normalized turn as the hub stored it, in full; reading it back trusts its stored shape."""

    index: int
    kind: TurnKind
    timestamp: str | None
    text: str
    tool: TurnToolCall | None
    thinking_redacted: bool
    sidechain: TurnSidechain | None
    truncated: bool

    @classmethod
    def of_stored(cls, stored: dict[str, Any]) -> TranscriptTurn:
        tool, sidechain = stored["tool"], stored["sidechain"]
        return cls(
            index=stored["index"],
            kind=stored["kind"],
            timestamp=stored["timestamp"],
            text=stored["text"],
            tool=TurnToolCall.of_stored(tool) if tool is not None else None,
            thinking_redacted=stored["thinking_redacted"],
            sidechain=TurnSidechain.of_stored(sidechain) if sidechain is not None else None,
            truncated=stored["truncated"],
        )

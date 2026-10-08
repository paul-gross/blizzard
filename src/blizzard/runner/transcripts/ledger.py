"""The transcript segment ledger repository seam.

Local per-segment state, never shipped as-is — distinct from the wire's own
``TranscriptSegmentRecord`` — plus the lane's own outbound buffer,
:class:`BufferedFact`'s counterpart."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import Literal, Protocol

from blizzard.foundation.roles import domain_model
from blizzard.runner.harness.identity import SessionReference

__all__ = [
    "CHUNK_BUDGET_EXCEEDED",
    "SEGMENT_TRANSITIONS",
    "BufferedTranscriptDelta",
    "IReadTranscriptLedgerRepository",
    "IWriteTranscriptLedgerRepository",
    "SegmentState",
    "SegmentTransition",
    "TranscriptBackfillLease",
    "TranscriptSegmentState",
    "TruncationMark",
    "TruncationMarkUpdate",
    "TruncationReason",
]

#: The one reason a segment stops shipping for good: the chunk's transcript budget is spent.
CHUNK_BUDGET_EXCEEDED = "chunk_budget_exceeded"


class TruncationReason(StrEnum):
    """Why a segment lost content — the never-silent ``truncated_reason`` vocabulary, ranked
    by :attr:`severity` so the displayed reason is always the worst one marked."""

    #: One tick's own source read came back incomplete.
    SOURCE_READ_TRUNCATED = "source_read_truncated"
    #: A record was shrunk to fit the per-record cap.
    RECORD_CAP_EXCEEDED = "record_cap_exceeded"
    #: A record could not be shrunk under the per-record cap at all.
    RECORD_UNSHIPPABLE = "record_unshippable"
    #: A lease-closure drain could not catch up with the source before its deadline.
    LEASE_CLOSURE_INCOMPLETE = "lease_closure_incomplete"
    #: The same loss on the backfill's own drain, where no lease closure is involved.
    BACKFILL_INCOMPLETE = "backfill_incomplete"
    #: The hub capped a record this runner shipped — the worst: the content left the runner.
    HUB_CAPPED = "hub_capped"
    #: The hub refused a record outright — its chunk and epoch are not this runner's — and stored none of it.
    HUB_REFUSED = "hub_refused"

    @property
    def severity(self) -> int:
        """Worst-of rank, mildest first."""
        return _TRUNCATION_SEVERITY[self]


_TRUNCATION_SEVERITY: dict[TruncationReason, int] = {
    TruncationReason.SOURCE_READ_TRUNCATED: 0,
    TruncationReason.RECORD_CAP_EXCEEDED: 1,
    TruncationReason.RECORD_UNSHIPPABLE: 2,
    TruncationReason.LEASE_CLOSURE_INCOMPLETE: 3,
    TruncationReason.BACKFILL_INCOMPLETE: 3,
    TruncationReason.HUB_CAPPED: 4,
    TruncationReason.HUB_REFUSED: 5,
}

#: Where one segment stands: still taking content, or sealed by its final marker.
SegmentState = Literal["open", "finalized"]

#: The writes a segment takes: content, the shipping stop, its finalization, and a truncation mark.
SegmentTransition = Literal["ship", "stop_shipping", "finalize", "mark_truncated"]

#: The transitions that write from each state; a truncation mark stays legal even after finalization.
SEGMENT_TRANSITIONS: dict[SegmentState, frozenset[SegmentTransition]] = {
    "open": frozenset({"ship", "stop_shipping", "finalize", "mark_truncated"}),
    "finalized": frozenset({"mark_truncated"}),
}


@domain_model
@dataclass(frozen=True)
class TranscriptSegmentState:
    """One row of the transcript segment ledger — local state, never shipped as-is, so named apart from
    the wire's ``TranscriptSegmentRecord``. ``normalizer_version`` starts at the source seam's "never ran"
    sentinel; ``truncated_reason``/``shipping_stopped_reason`` are independent, the former never latching.
    :data:`SEGMENT_TRANSITIONS` declares which writes it takes from which state."""

    segment_id: str
    chunk_id: str
    node_id: str
    epoch: int
    generation: int
    lease_id: str
    session_id: str
    cursor: str | None
    shipped_bytes: int
    shipped_turns: int
    normalizer_version: str
    harness_version: str | None
    truncated_reason: str | None
    shipping_stopped_reason: str | None
    #: Set only on a re-ship: the segment this one replaces on the hub.
    supersedes: str | None
    finalized_at: datetime | None
    stamped_at: datetime
    harness_id: str
    #: Frozen at segment open from the lease's own resolved pair; ``None`` when unresolved.
    model: str | None
    effort: str | None
    #: The worker's working directory, frozen at segment open; ``None`` when unknown.
    spawn_cwd: str | None
    #: agent_id -> spawning `tool_use_id`, accumulated across every window
    #: this segment has read; empty until one names a pair.
    agent_tool_use_ids: dict[str, str] = field(default_factory=dict)

    @property
    def session(self) -> SessionReference:
        return SessionReference(self.harness_id, self.session_id)

    @property
    def state(self) -> SegmentState:
        return self._state()

    def _state(self) -> SegmentState:
        return "finalized" if self.finalized_at is not None else "open"

    @property
    def final(self) -> bool:
        """Sealed by its final marker — no more content will ever ship for it."""
        return self._final()

    def _final(self) -> bool:
        return self.finalized_at is not None

    @property
    def shipping_stopped(self) -> bool:
        """Stopped for good past the chunk's transcript budget: ships nothing more."""
        return self._shipping_stopped()

    def _shipping_stopped(self) -> bool:
        return self.shipping_stopped_reason is not None

    @property
    def truncated(self) -> bool:
        """Lost content, by either path: a marked truncation or a shipping stop."""
        return self._truncated()

    def _truncated(self) -> bool:
        return self.truncated_reason is not None or self.shipping_stopped_reason is not None

    @property
    def lost_to_cap(self) -> bool:
        """Content the hub will never hold, by a cap on either side — the hub capped a record,
        or this runner stopped shipping past the chunk budget."""
        return self._lost_to_cap()

    def _lost_to_cap(self) -> bool:
        return self.truncated_reason == TruncationReason.HUB_CAPPED or self.shipping_stopped

    @property
    def accepts_content(self) -> bool:
        """Whether a content write (deltas or a cursor advance) applies — ``False`` once finalized."""
        return self.accepts("ship")

    def accepts(self, transition: SegmentTransition) -> bool:
        """Whether ``transition`` writes from this segment's state, per :data:`SEGMENT_TRANSITIONS`."""
        return transition in SEGMENT_TRANSITIONS[self.state]


@domain_model
@dataclass(frozen=True)
class TruncationMarkUpdate:
    """What one truncation mark writes: the displayed reason and its severity when they change,
    the warned-reason latch when it grows. ``newly_warned`` is whether the mark owes its warning."""

    truncated_reason: str | None
    truncated_reason_severity: int | None
    reasons_warned: tuple[str, ...] | None
    newly_warned: bool

    @property
    def changes(self) -> bool:
        return self._changes()

    def _changes(self) -> bool:
        return self.truncated_reason is not None or self.reasons_warned is not None


@domain_model
@dataclass(frozen=True)
class TruncationMark:
    """A segment's truncation display and warn latch, as stored. ``current_severity`` is
    ``None`` for a row that took its reason before severities were recorded — incomparable."""

    current_reason: str | None
    current_severity: int | None
    reasons_warned: tuple[str, ...]

    def apply(self, reason: str, severity: int) -> TruncationMarkUpdate:
        """Mark ``reason``. The display is worst-of by ``severity`` (a tie or an incomparable
        current takes the new reason); the warning is latched per reason, independent of the
        display — a reason already warned never re-warns."""
        newly_warned = reason not in self.reasons_warned
        replaces = self.current_reason != reason and (
            self.current_reason is None or self.current_severity is None or severity >= self.current_severity
        )
        return TruncationMarkUpdate(
            truncated_reason=reason if replaces else None,
            truncated_reason_severity=severity if replaces else None,
            reasons_warned=(*self.reasons_warned, reason) if newly_warned else None,
            newly_warned=newly_warned,
        )


@domain_model
@dataclass(frozen=True)
class BufferedTranscriptDelta:
    """One pending record in the transcript lane's own buffer — ``BufferedFact``'s counterpart. Non-final
    ``payload`` is a ``TranscriptSegmentRecord``'s fields (minus ``seq``/``runner_id``) as JSON; a final
    one is just ``{"segment_id": ...}``. ``final`` mirrors the payload's flag, driving ack-time
    keep-vs-delete."""

    seq: int
    segment_id: str
    chunk_id: str
    final: bool
    payload: str
    created_at: datetime


@domain_model
@dataclass(frozen=True)
class TranscriptBackfillLease:
    """One session-bearing lease the backfill may import, with whether that
    session already holds a segment. The dedupe key is the *session*: a pre-epic session
    resumed across leases left one merged file, which imports once."""

    lease_id: str
    chunk_id: str
    node_id: str
    epoch: int
    session_id: str
    has_segment: bool
    harness_id: str

    @property
    def session(self) -> SessionReference:
        return SessionReference(self.harness_id, self.session_id)


class IReadTranscriptLedgerRepository(Protocol):
    """Read-only transcript segment ledger queries."""

    def transcript_segment(self, segment_id: str) -> TranscriptSegmentState | None:
        """The segment by id, or ``None``."""
        ...

    def transcript_segments(self, segment_ids: Sequence[str]) -> dict[str, TranscriptSegmentState]:
        """:meth:`transcript_segment` for every id in ``segment_ids``, in one grouped read.
        An id with no row is absent, exactly as the singular answers ``None`` for it."""
        ...

    def open_transcript_segments(self) -> list[TranscriptSegmentState]:
        """Every segment with no final marker yet, across all leases."""
        ...

    def open_transcript_segments_for_lease(self, lease_id: str) -> list[TranscriptSegmentState]:
        """This lease's own open segments — :meth:`open_transcript_segments` narrowed to one
        lease (`bzh:bulk-reconstitution`), rather than reading every open segment in the
        store and filtering to one lease in Python. A lease ordinarily holds at most one,
        but a re-ship can leave a second beside its source."""
        ...

    def transcript_segments_for_chunk(self, chunk_id: str) -> list[TranscriptSegmentState]:
        """The chunk's segment ledger rows, oldest first, open or finalized alike — the
        runner-plane's chunk-scoped segment index read (runner-node-grouped-transcripts).
        A chunk this store holds no lease for returns ``[]``."""
        ...

    def chunk_transcript_shipped_bytes(self, chunk_ids: Sequence[str]) -> dict[str, int]:
        """Sum of ``shipped_bytes`` across each id in ``chunk_ids``'s own segments, open or
        finalized, in one grouped read (`bzh:bulk-reconstitution`) — the running total the
        64 MB per-chunk budget is measured against. A chunk with no segments at all is
        absent, read the same as a ``0`` sum."""
        ...

    def outstanding_transcript_buffer_bytes(self) -> int:
        """Sum of ``payload`` bytes across every UNACKED row of the transcript outbound
        buffer, across every segment — the resident total a prolonged hub outage can leave
        unbounded in SQLite absent a bound on it. Distinct from
        :meth:`chunk_transcript_shipped_bytes`, which bounds a queried chunk's own SHIPPED
        total, not the buffer's own resident total."""
        ...

    def has_unshipped_transcript_content(self, chunk_id: str) -> bool:
        """Whether this chunk holds an UNACKED **content** row in the transcript outbound
        buffer. Final markers are excluded: a pending one carries no turns, so the hub's
        copy is already complete. An existence check, not
        :meth:`pending_transcript_outbound`'s payload-materializing list read."""
        ...

    def pending_transcript_outbound(self, *, limit: int | None = None) -> list[BufferedTranscriptDelta]:
        """The unacked transcript buffer, FIFO by seq — the drain's own lane.

        ``limit`` bounds the query itself, not just what the caller iterates — a large
        backlog's full payload set (up to the per-record cap each) is otherwise materialized
        before any per-run bound the caller applies is ever consulted."""
        ...

    def transcript_backfill_leases(self) -> list[TranscriptBackfillLease]:
        """Every lease that ever recorded a session id, oldest first. This store is the only
        source: the harness directory holds the operator's own sessions too, and a sweep of it
        could never tell them apart."""
        ...


class IWriteTranscriptLedgerRepository(IReadTranscriptLedgerRepository, Protocol):
    """Read-write transcript segment ledger store — held only by the domain."""

    def mark_transcript_record_truncated(self, segment_id: str, *, reason: str, severity: int) -> bool:
        """Note that one shipped record was shrunk in place under the per-record cap —
        informational only. Latches per ``(segment_id, reason)``: the SAME reason
        recurring never re-warns; a DIFFERENT one always does, regardless of what currently
        displays. ``severity`` ranks ``reason`` against this method's other callers — the
        store keeps whichever arrived with the highest severity as the displayed one."""
        ...

    def stop_transcript_segment_shipping(self, segment_id: str, *, reason: str) -> bool:
        """Permanently stop shipping this segment's content — the per-chunk 64 MB budget
        breached. Idempotent: keeps its first reason, and a no-op on a finalized segment. Returns whether this call
        actually set the field."""
        ...

    def mark_sidechain_dropped_warned(self, segment_id: str, *, agent_id: str | None) -> bool:
        """Latch the dropped-sidechain fact-lane warning per (segment, agent_id): a subagent
        conversation can outlive one pump window, so this must not re-warn every tick it
        stays unlinked. Returns whether this is the first warning for this agent."""
        ...

    def record_transcript_deltas(
        self,
        *,
        segment_id: str,
        chunk_id: str,
        cursor: str | None,
        shipped_bytes: int,
        shipped_turns: int,
        normalizer_version: str,
        harness_version: str | None,
        payloads: list[str],
        created_at: datetime,
        agent_tool_use_ids: dict[str, str] | None = None,
    ) -> list[int]:
        """Advance a segment's cursor/shipped counts/version stamp and atomically enqueue
        ``len(payloads)`` buffer rows — ONE transaction, so a batch split
        into several records still advances the cursor exactly once, and a crash loses
        neither the cursor advance nor any record. Returns their seqs, in payload order —
        ``[]``, writing nothing, when the segment is finalized (:data:`SEGMENT_TRANSITIONS`)."""
        ...

    def open_transcript_segment(
        self,
        *,
        chunk_id: str,
        node_id: str,
        epoch: int,
        generation: int,
        lease_id: str,
        stamped_at: datetime,
        session: SessionReference,
        supersedes: str | None = None,
        spawn_cwd: str | None = None,
    ) -> str:
        """Stamp a segment boundary outside a spawn and return its id, cursor unset so the
        session is read from its start. ``supersedes`` is the re-ship's own pointer at the
        segment this one replaces on the hub. ``spawn_cwd`` is the worker's working directory, when known."""
        ...

    def finalize_transcript_segment(self, segment_id: str, *, finalized_at: datetime) -> bool:
        """Close one segment out on its own, enqueuing its single final marker in the same
        transaction — :meth:`~blizzard.runner.leases.record.IWriteLeaseRecordRepository.record_closure`'s
        per-segment half, for a segment whose lease closed long before it existed. ``False``
        when it was already finalized."""
        ...

    def advance_transcript_cursor(
        self,
        segment_id: str,
        *,
        cursor: str,
        normalizer_version: str,
        harness_version: str | None,
        agent_tool_use_ids: dict[str, str] | None = None,
    ) -> None:
        """Advance a segment's read cursor (and version stamp) with nothing to enqueue — a
        window that moved the source's read position but produced no turn (e.g. a run of
        control records), which still must not be re-read next tick. Unlike
        :meth:`record_transcript_deltas`, no outbound row: there is no record to ship, only
        progress to remember. A no-op on a finalized segment."""
        ...

    def ack_transcript_outbound(self, seq: int, *, acked_at: datetime) -> None:
        """Ack a buffered transcript row. A ``delta`` row is pruned outright (up to the
        per-record cap each, nothing reads one acked); a ``final`` row stays, marked acked, as
        the segment's exactly-once finalization receipt."""
        ...

    def ack_transcript_outbound_batch(self, seqs: list[int], *, acked_at: datetime) -> None:
        """Ack every seq in ``seqs`` in one transaction, same delta/final
        split as :meth:`ack_transcript_outbound`."""
        ...

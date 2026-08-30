"""The transcript segment ledger repository seam (issue #246, blizzard#410).

Local per-segment state, never shipped as-is — distinct from the wire's own
``TranscriptSegmentRecord`` (blizzard#247) — plus the lane's own outbound buffer (D3),
:class:`BufferedFact`'s counterpart."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Protocol

__all__ = [
    "BufferedTranscriptDelta",
    "IReadTranscriptLedgerRepository",
    "IWriteTranscriptLedgerRepository",
    "TranscriptBackfillLease",
    "TranscriptSegmentLedgerRow",
]


@dataclass(frozen=True)
class TranscriptSegmentLedgerRow:
    """One row of the transcript segment ledger (issue #246, D2) — local state, never shipped
    as-is, and so named apart from the wire's own ``TranscriptSegmentRecord`` (blizzard#247).
    ``normalizer_version`` is never ``None``, starting at the source seam's "never ran"
    sentinel. ``truncated_reason``/``shipping_stopped_reason`` are independent: the former never latches."""

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
    #: Set only on a re-ship (blizzard#250): the segment this one replaces on the hub.
    supersedes: str | None
    finalized_at: datetime | None
    stamped_at: datetime
    #: agent_id -> spawning `tool_use_id` (blizzard#338), accumulated across every window
    #: this segment has read; empty until one names a pair.
    agent_tool_use_ids: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class BufferedTranscriptDelta:
    """One pending record in the transcript lane's own buffer (D3) — ``BufferedFact``'s
    counterpart. Non-final ``payload`` is a ``TranscriptSegmentRecord``'s fields (minus
    ``seq``/``runner_id``) as JSON; a final one is just ``{"segment_id": ...}``. ``final``
    mirrors the payload's own flag, driving ack-time keep-vs-delete."""

    seq: int
    segment_id: str
    chunk_id: str
    final: bool
    payload: str
    created_at: datetime


@dataclass(frozen=True)
class TranscriptBackfillLease:
    """One session-bearing lease the backfill may import (blizzard#250), with whether that
    session already holds a segment. The dedupe key is the *session*: a pre-epic session
    resumed across leases left one merged file, which imports once."""

    lease_id: str
    chunk_id: str
    node_id: str
    epoch: int
    session_id: str
    has_segment: bool


class IReadTranscriptLedgerRepository(Protocol):
    """Read-only transcript segment ledger queries (held by read-path edges)."""

    def transcript_segment(self, segment_id: str) -> TranscriptSegmentLedgerRow | None:
        """The segment by id, or ``None`` — the pump and drain's per-segment read (issue #246)."""
        ...

    def open_transcript_segments(self) -> list[TranscriptSegmentLedgerRow]:
        """Segments with no final marker yet — the pump's per-tick work list (issue #246)."""
        ...

    def transcript_segments_for_chunk(self, chunk_id: str) -> list[TranscriptSegmentLedgerRow]:
        """The chunk's segment ledger rows, oldest first, open or finalized alike — the
        runner-plane's chunk-scoped segment index read (D6, runner-node-grouped-transcripts).
        A chunk this store holds no lease for returns ``[]``."""
        ...

    def chunk_transcript_shipped_bytes(self, chunk_id: str) -> int:
        """Sum of ``shipped_bytes`` across every one of this chunk's segments, open or
        finalized — the running total the 64 MB per-chunk budget (D4) is measured against."""
        ...

    def outstanding_transcript_buffer_bytes(self) -> int:
        """Sum of ``payload`` bytes across every UNACKED row of the transcript outbound
        buffer, across every segment (F8, review round 7) — the pump's own backpressure
        gate against a prolonged hub outage leaving unbounded content resident in SQLite.
        Distinct from :meth:`chunk_transcript_shipped_bytes`, which bounds one chunk's
        SHIPPED total, not the buffer's own resident total."""
        ...

    def has_unshipped_transcript_content(self, chunk_id: str) -> bool:
        """Whether this chunk holds an UNACKED **content** row in the transcript outbound
        buffer (issue #249) — the "not yet acked by the hub" half of the panel's home
        selection. Final markers are excluded deliberately: a pending one carries no turns,
        so the hub's copy is already complete. An existence check, not
        :meth:`pending_transcript_outbound`'s payload-materializing list read."""
        ...

    def pending_transcript_outbound(self, *, limit: int | None = None) -> list[BufferedTranscriptDelta]:
        """The unacked transcript buffer, FIFO by seq — the drain's own lane (D3).

        ``limit`` bounds the query itself, not just what the caller iterates — a large
        backlog's full payload set (up to the per-record cap each) is otherwise materialized
        before any per-run bound the caller applies is ever consulted."""
        ...

    def transcript_backfill_leases(self) -> list[TranscriptBackfillLease]:
        """Every lease that ever recorded a session id, oldest first — the backfill's work
        list (blizzard#250). This store is the only source: the harness directory holds the
        operator's own sessions too, and a sweep of it could never tell them apart."""
        ...


class IWriteTranscriptLedgerRepository(IReadTranscriptLedgerRepository, Protocol):
    """Read-write transcript segment ledger store — held only by the domain."""

    def mark_transcript_record_truncated(self, segment_id: str, *, reason: str, severity: int) -> bool:
        """Note that one shipped record was shrunk in place (D4's per-record cap) —
        informational only. Latches per ``(segment_id, reason)`` (F2): the SAME reason
        recurring never re-warns; a DIFFERENT one always does, regardless of what currently
        displays. ``severity`` ranks ``reason`` against this method's other callers — the
        store keeps whichever arrived with the highest severity as the displayed one."""
        ...

    def stop_transcript_segment_shipping(self, segment_id: str, *, reason: str) -> bool:
        """Permanently stop shipping this segment's content — the per-chunk 64 MB budget
        breached (D4). The only field :class:`TranscriptPump`'s guard reads; idempotent,
        keeps its first reason. Returns whether this call actually set the field."""
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
        ``len(payloads)`` buffer rows (issue #246; F1) — ONE transaction, so a batch split
        into several records still advances the cursor exactly once, and a crash loses
        neither the cursor advance nor any record. Returns their seqs, in payload order."""
        ...

    def open_transcript_segment(
        self,
        *,
        chunk_id: str,
        node_id: str,
        epoch: int,
        generation: int,
        lease_id: str,
        session_id: str,
        stamped_at: datetime,
        supersedes: str | None = None,
    ) -> str:
        """Stamp a segment boundary outside a spawn and return its id (blizzard#250), cursor
        unset so the pump reads the session from the start. Every boundary the *live* lane
        stamps stays :meth:`~blizzard.runner.domain.leases.IWriteLeaseRepository.record_spawn`'s;
        this one is the backfill's alone. ``supersedes`` is the re-ship's own pointer at the
        segment this one replaces on the hub."""
        ...

    def finalize_transcript_segment(self, segment_id: str, *, finalized_at: datetime) -> bool:
        """Close one segment out on its own, enqueuing its single final marker in the same
        transaction — :meth:`~blizzard.runner.domain.leases.IWriteLeaseRepository.record_closure`'s
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
        progress to remember."""
        ...

    def ack_transcript_outbound(self, seq: int, *, acked_at: datetime) -> None:
        """Ack a buffered transcript row — the drain's own ack (D3). A ``delta`` row is
        pruned outright (up to the per-record cap each, nothing reads one acked); a ``final`` row
        stays, marked acked — its own tiny row is the exactly-once receipt
        :class:`~blizzard.tools.invariants.TranscriptSegmentFinalizedExactlyOnce`
        checks for."""
        ...

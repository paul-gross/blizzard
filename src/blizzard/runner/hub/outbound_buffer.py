"""The hub-bound store-and-forward outbound buffer repository seam."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from blizzard.foundation.event_log import EVENT_LOG_SEVERITY, EventLogKind
from blizzard.foundation.roles import dto

__all__ = [
    "BufferedFact",
    "IReadOutboundRepository",
    "IWriteOutboundRepository",
    "OutboundFactEntry",
    "event_payload",
]


@dto
@dataclass(frozen=True)
class BufferedFact:
    """One pending hub-bound fact in the store-and-forward buffer."""

    seq: int
    kind: str
    chunk_id: str | None
    lease_id: str | None
    payload: str
    created_at: datetime


@dto
@dataclass(frozen=True)
class OutboundFactEntry:
    """One hub-bound fact off the outbound buffer, acked or not. The same table as
    :class:`BufferedFact`, read as a ledger: ``acked_at`` kept, ``payload`` dropped."""

    seq: int
    kind: str
    chunk_id: str | None
    lease_id: str | None
    created_at: datetime
    acked_at: datetime | None


def event_payload(
    *,
    kind: EventLogKind,
    chunk_id: str | None,
    lease_id: str | None,
    node_name: str | None,
    message: str,
    detail: Mapping[str, object] | None,
) -> dict[str, object]:
    """The ``event.recorded`` payload — one shape for every operational event this runner buffers."""
    return {
        "severity": EVENT_LOG_SEVERITY[kind],
        "kind": kind,
        "chunk_id": chunk_id,
        "lease_id": lease_id,
        "node_name": node_name,
        "message": message,
        "detail": detail,
    }


class IReadOutboundRepository(Protocol):
    """Read-only outbound-buffer queries (held by read-path edges)."""

    def pending_submission_lease_ids(self) -> set[str]:
        """Lease ids with an unacked ``completion.submitted`` or ``decision.submitted``
        fact in the buffer.

        The judged-and-buffered skip set, so a node-step's outcome is elicited exactly once
        while the flush is pending."""
        ...

    def pending_outbound(self, *, limit: int | None = None) -> list[BufferedFact]:
        """The unacked outbound buffer, FIFO by seq; unbounded when ``limit`` is ``None``.

        A given ``limit`` bounds the query itself, not just what the caller iterates — a
        large backlog's full payload set is otherwise materialized before any per-run bound
        the caller applies is ever consulted."""
        ...

    def pending_outbound_count(self) -> int:
        """How many facts are unacked, across the WHOLE backlog — never truncated by
        :meth:`pending_outbound`'s own ``limit``, and never materializing a payload row
        just to count it."""
        ...

    def recent_outbound(self, limit: int) -> list[OutboundFactEntry]:
        """The newest ``limit`` outbound facts, acked or not, newest first — the local fact log."""
        ...


class IWriteOutboundRepository(IReadOutboundRepository, Protocol):
    """Read-write outbound-buffer store — held only by the domain."""

    def enqueue_outbound(
        self, *, kind: str, chunk_id: str | None, lease_id: str | None, payload: str, created_at: datetime
    ) -> int:
        """Append a hub-bound fact to the store-and-forward buffer; return its seq."""
        ...

    def ack_outbound(self, seq: int, *, acked_at: datetime) -> None:
        """Mark a buffered fact delivered — a semantic rejection acks too."""
        ...

    def ack_outbound_batch(self, seqs: list[int], *, acked_at: datetime) -> None:
        """Mark every seq in ``seqs`` delivered, in one transaction, so a
        crash mid-batch never acks part of one delivered run."""
        ...

    def prune_outbound(self, *, now: datetime) -> int:
        """Delete acked rows older than the store's own retention window, but
        only below the lowest still-pending seq — an acked row interleaved above a pending
        one always survives, so the retained buffer stays gapless from the pending floor
        upward. Returns the number of rows pruned."""
        ...

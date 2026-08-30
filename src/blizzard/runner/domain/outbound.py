"""The hub-bound store-and-forward outbound buffer repository seam (blizzard#410)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

__all__ = [
    "BufferedFact",
    "IReadOutboundRepository",
    "IWriteOutboundRepository",
    "OutboundFactRecord",
]


@dataclass(frozen=True)
class BufferedFact:
    """One pending hub-bound fact in the store-and-forward buffer."""

    seq: int
    kind: str
    chunk_id: str | None
    lease_id: str | None
    payload: str
    created_at: datetime


@dataclass(frozen=True)
class OutboundFactRecord:
    """One hub-bound fact off the outbound buffer, acked or not. The same table as
    :class:`BufferedFact`, read as a ledger: ``acked_at`` kept, ``payload`` dropped."""

    seq: int
    kind: str
    chunk_id: str | None
    lease_id: str | None
    created_at: datetime
    acked_at: datetime | None


class IReadOutboundRepository(Protocol):
    """Read-only outbound-buffer queries (held by read-path edges)."""

    def pending_submission_lease_ids(self) -> set[str]:
        """Lease ids with an unacked ``completion.submitted`` or ``decision.submitted``
        fact in the buffer.

        ADVANCE's skip set, so a node-step's outcome is elicited exactly once while the
        flush is pending."""
        ...

    def pending_outbound(self) -> list[BufferedFact]:
        """The unacked outbound buffer, FIFO by seq."""
        ...

    def recent_outbound(self, limit: int) -> list[OutboundFactRecord]:
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

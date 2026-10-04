"""The operator-request repository seams — pending requeues and lease attachments.

The claim and dormant steps read these facts and the operator services write them; the
seams live with the lease concept so both sides name them without an upward edge."""

from __future__ import annotations

from datetime import datetime
from typing import Protocol

__all__ = [
    "IReadAttachmentRepository",
    "IReadRequeueRepository",
    "IWriteAttachmentRepository",
    "IWriteRequeueRepository",
]


class IReadRequeueRepository(Protocol):
    """Read-only requeue queries (held by read-path edges)."""

    def pending_requeue_chunk_ids(self) -> set[str]:
        """Every chunk id carrying a requeue mark not yet consumed by a later lease mint.

        The mark is consumed by the next lease mint for the chunk, whose ``created_at``
        lands at or after the requeue."""
        ...


class IWriteRequeueRepository(IReadRequeueRepository, Protocol):
    """Read-write requeue store — held only by the domain."""

    def record_requeue(self, *, chunk_id: str, at: datetime) -> None:
        """Append the clearing fact for a chunk's local needs_human hold.

        Recorded before anything else runs (``bzh:crash-correctness``): the fact alone is
        durable the instant this returns, and is read back via
        :meth:`pending_requeue_chunk_ids` — this call never spawns anything itself."""
        ...


class IReadAttachmentRepository(Protocol):
    """Read-only attachment queries (held by read-path edges)."""

    def attachments_for_lease(self, lease_id: str) -> dict[str, str]:
        """The lease's explicit artifact submissions, newest content per ``name``.
        Append-only, latest-wins-per-``(lease_id, name)``: a re-attach of
        the same name reads back as the replacement, never a duplicate."""
        ...

    def attachment_names_for_lease(self, lease_id: str) -> set[str]:
        """Just the names attached for ``lease_id`` — the produces-coverage check's own lean
        read: it only ever needs to know WHICH names are attached, never their
        content, so this skips fetching and materializing ``attachments_for_lease``'s values."""
        ...


class IWriteAttachmentRepository(IReadAttachmentRepository, Protocol):
    """Read-write attachment store — held only by the domain."""

    def record_attachment(
        self,
        *,
        lease_id: str,
        chunk_id: str,
        node_id: str,
        epoch: int,
        name: str,
        content: str,
        attached_at: datetime,
    ) -> None:
        """Append a worker's explicit artifact submission for ``name``, a
        single committed transaction so it survives a ``kill -9`` before the completion
        submission reads it. Append-only: a later call for the same ``(lease_id, name)``
        is a correction, read back as the replacement, never merged."""
        ...

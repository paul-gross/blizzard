"""The lease-record repository seam — mint, closure, and lookups by lease or chunk
identity."""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from blizzard.runner.domain.leases import ClosedLeaseRecord, LeaseRecord, NewLease


class IReadLeaseRecordRepository(Protocol):
    """Read-only lease-identity queries — mint, closure, and lookups by lease or chunk
    (held by read-path edges)."""

    def list_active_leases(self) -> list[LeaseRecord]:
        """Leases with no closure fact — the attempts currently in flight."""
        ...

    def active_lease_for_chunk(self, chunk_id: str) -> LeaseRecord | None:
        """The chunk's single active lease, if any (P6: at most one — MAX_AGENTS math)."""
        ...

    def active_lease(self, lease_id: str) -> LeaseRecord | None:
        """The lease by id iff it is still active (no closure fact), else ``None``.

        The flusher's ack-idempotency check: an already-closed lease means the completion
        applied on an earlier flush whose ack was lost.
        """
        ...

    def latest_lease_for_chunk(self, chunk_id: str) -> LeaseRecord | None:
        """The chunk's most-recently-minted lease, active or closed (issue #52).

        Unlike :meth:`active_lease_for_chunk`, spans closed leases too: a takeover can be
        requested with no active lease left, and the closed one still carries the session
        id it resumes."""
        ...

    def lease(self, lease_id: str) -> LeaseRecord | None:
        """The lease by id, regardless of closure — the transcript read (issue #29).

        Distinct from :meth:`active_lease`: a transcript outlives its lease.
        """
        ...

    def list_closed_leases(self, limit: int) -> list[ClosedLeaseRecord]:
        """The most recently closed leases, newest first — the panel's recent-history
        read (issue #29).

        ``limit`` bounds rows returned, never how long a closure fact lives on disk.
        """
        ...

    def attempt_count(self, chunk_id: str, node_id: str) -> int:
        """How many leases have been minted for this chunk at this node (retry budget).

        Excludes an attempt an operator's restart preempted (issue #370) — that attempt was
        superseded rather than spent, so it does not carry the node toward exhaustion."""
        ...

    def latest_epoch(self, chunk_id: str) -> int:
        """The highest lease epoch minted for this chunk, or 0 — the fence source."""
        ...

    def lease_ids_for_chunk(self, chunk_id: str) -> list[str]:
        """Every lease id ever minted for this chunk, active or closed (issue #58).

        A chunk's tenure can span several node-steps and retries, each its own lease —
        this is the release-time read that finds every one of them, not just the
        currently-active lease."""
        ...


class IWriteLeaseRecordRepository(IReadLeaseRecordRepository, Protocol):
    """Read-write lease-identity store — held only by the domain (the loop steps)."""

    def record_lease(self, lease: NewLease) -> None:
        """Persist a minted lease and its node context, atomically."""
        ...

    def record_closure(
        self,
        *,
        lease_id: str,
        chunk_id: str,
        node_id: str,
        reason: str,
        closed_at: datetime,
        event_kind: str | None = None,
        event_payload: str | None = None,
    ) -> int | None:
        """Close a lease — a clean transition or a failure/escalation.

        When ``event_kind``/``event_payload`` are given (issue #125), the event is
        enqueued to the outbound buffer **in the same transaction** as the closure —
        the two land together or not at all; return its seq, ``None`` when no event."""
        ...

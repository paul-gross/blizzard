"""The lease-record repository seam — mint, closure, and lookups by lease or chunk
identity."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from blizzard.foundation.roles import domain_model
from blizzard.runner.leases.model import ClosedLease, Lease, NewMint


@domain_model
@dataclass(frozen=True)
class ClosureFact:
    """One hub-bound fact a closure carries into the outbound buffer — its fact kind and its
    already-rendered JSON payload."""

    kind: str
    payload: str


class IReadLeaseRecordRepository(Protocol):
    """Read-only lease-identity queries — mint, closure, and lookups by lease or chunk."""

    def list_active_leases(self) -> list[Lease]:
        """Leases with no closure fact — the attempts currently in flight."""
        ...

    def active_lease_chunk_ids(self) -> set[str]:
        """The chunk id of every lease with no closure fact — a column projection, never
        reconstituting each :class:`Lease` through the lease+context join just to take its
        ``chunk_id`` (`bzh:bulk-reconstitution`)."""
        ...

    def active_lease_for_chunk(self, chunk_id: str) -> Lease | None:
        """The chunk's single active lease, if any (at most one)."""
        ...

    def active_lease(self, lease_id: str) -> Lease | None:
        """The lease by id iff it is still active (no closure fact), else ``None``.

        A closed lease reads ``None``.
        """
        ...

    def latest_lease_for_chunk(self, chunk_id: str) -> Lease | None:
        """The chunk's most-recently-minted lease, active or closed.

        Unlike :meth:`active_lease_for_chunk`, spans closed leases too — but, unlike
        :meth:`latest_lease_with_session_for_chunk`, may name a session-less mint."""
        ...

    def latest_lease_with_session_for_chunk(self, chunk_id: str) -> Lease | None:
        """The chunk's most-recently-minted lease that carries a session, active or closed."""
        ...

    def lease(self, lease_id: str) -> Lease | None:
        """The lease by id, regardless of closure.

        Distinct from :meth:`active_lease`: a transcript outlives its lease.
        """
        ...

    def list_closed_leases(self, limit: int) -> list[ClosedLease]:
        """The most recently closed leases, newest first.

        ``limit`` bounds rows returned, never how long a closure fact lives on disk.
        """
        ...

    def attempt_count(self, chunk_id: str, node_id: str) -> int:
        """How many leases have been minted for this chunk at this node in the current visit (retry budget).

        A visit ends when the node-step completes (``transitioned`` or ``parked``); a node re-entered
        through an edge starts its budget afresh.

        Excludes an attempt an operator's restart preempted — that attempt was
        superseded rather than spent, so it does not carry the node toward exhaustion."""
        ...

    def latest_epoch(self, chunk_id: str) -> int:
        """The highest lease epoch minted for this chunk, or 0 — the fence source."""
        ...

    def has_lease_in_binding_tenure(self, chunk_id: str, bound_at: datetime) -> bool:
        """Whether a local lease was minted since the current binding began.

        Release-time epoch floors distinguish an earlier tenure even if clock instants coincide."""
        ...

    def lease_ids_for_chunk(self, chunk_id: str) -> list[str]:
        """Every lease id ever minted for this chunk, active or closed.

        A chunk's tenure can span several node-steps and retries, each its own lease —
        this is the release-time read that finds every one of them, not just the
        currently-active lease."""
        ...


class IReadChunkLeaseRepository(IReadLeaseRecordRepository, Protocol):
    """The lease record plus one chunk's whole lease history — the lease list's chunk filter."""

    def chunk_leases(self, chunk_id: str) -> list[Lease | ClosedLease]:
        """Every lease of this chunk, open (a :class:`Lease`) or closed (a :class:`ClosedLease`),
        oldest mint first (``created_at``, then ``epoch``). Unbounded: one chunk's leases bound it."""
        ...


class IWriteLeaseRecordRepository(IReadChunkLeaseRepository, Protocol):
    """Read-write lease-identity store — held only by the domain (the loop steps)."""

    def record_mint(self, mint: NewMint) -> int:
        """Persist a lease mint whole, in one transaction: the lease and its context, the mint owner
        (when one was chosen), the token hash, and the ``lease.minted`` outbound row. Returns that row's
        ``seq``. Either every write lands or none does."""
        ...

    def record_closure(
        self,
        *,
        lease_id: str,
        chunk_id: str,
        node_id: str,
        reason: str,
        closed_at: datetime,
        facts: Sequence[ClosureFact] = (),
        escalation_cause: str | None = None,
    ) -> list[int]:
        """Close a lease — a clean transition or a failure/escalation.

        Each of ``facts`` is enqueued to the outbound buffer, in the order given, **in the same
        transaction** as the closure; returns their seqs in that order (empty when none).
        ``escalation_cause`` records why an escalating closure escalated, durably beside it."""
        ...

"""The durable transcript invocation-boundary repository seam.

One row per fleet-driven invocation — a worker spawn generation, a resume generation, a
judgement, or a nudge — recording the transcript position it started from, durably BEFORE
the invocation launches. Runner-local only: never delivered to the hub, never read outside
the runner plane (``bzh:runner-plane-transcript-reads``)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Literal, Protocol

__all__ = [
    "WORKER_STARTING_KINDS",
    "IReadInvocationBoundaryRepository",
    "IWriteInvocationBoundaryRepository",
    "InvocationBoundaryKind",
    "InvocationBoundaryRecord",
    "InvocationBoundaryStart",
]

#: The four invocation kinds a boundary ever names — a nudge's own, distinct from ``resume``.
InvocationBoundaryKind = Literal["spawn", "resume", "judge", "nudge"]

#: Worker-starting kinds, tried in order (``"judge"`` excluded) — shared with the invariant checker.
WORKER_STARTING_KINDS: tuple[InvocationBoundaryKind, ...] = ("spawn", "resume", "nudge")


@dataclass(frozen=True)
class InvocationBoundaryRecord:
    """One invocation's durable start marker — its true start, never rewritten. ``start_position``
    is the opaque ``TranscriptPosition.token`` minted just before launch, or ``None`` — a fresh
    session's own beginning sentinel; ``start_unreadable`` marks a failed tail read instead."""

    lease_id: str
    chunk_id: str
    node_id: str
    epoch: int
    generation: int
    kind: InvocationBoundaryKind
    start_position: str | None
    opened_at: datetime
    closed_at: datetime | None
    closed_reason: str | None
    start_unreadable: bool = False


@dataclass(frozen=True)
class InvocationBoundaryStart:
    """Where an invocation boundary's range currently starts: the newest advance's own
    values, or the marker's when there is none. ``at`` is when that start was recorded."""

    start_position: str | None
    start_unreadable: bool
    at: datetime


class IReadInvocationBoundaryRepository(Protocol):
    """Read-only invocation-boundary queries (held by read-path edges)."""

    def boundary(self, lease_id: str, generation: int, kind: InvocationBoundaryKind) -> InvocationBoundaryRecord | None:
        """This exact invocation's marker — its true start, never rewritten — or ``None``
        when it was never opened."""
        ...

    def current_start(
        self, lease_id: str, generation: int, kind: InvocationBoundaryKind
    ) -> InvocationBoundaryStart | None:
        """Where this invocation's range currently starts — the newest advance's start, the
        marker's own when there is none, or ``None`` when it was never opened. The read
        interrupted-usage recovery keys a standing judge's range from."""
        ...

    def open_boundaries_for_lease(self, lease_id: str) -> list[InvocationBoundaryRecord]:
        """This lease's boundaries with no ``closed_at`` yet, in ``opened_at`` order —
        empty once the lease has closed (``bzh:open-facts-declare-closure``)."""
        ...


class IWriteInvocationBoundaryRepository(IReadInvocationBoundaryRepository, Protocol):
    """Read-write invocation-boundary store — held only by the domain."""

    def record_boundary_open(
        self,
        *,
        lease_id: str,
        chunk_id: str,
        node_id: str,
        epoch: int,
        generation: int,
        kind: InvocationBoundaryKind,
        start_position: str | None,
        opened_at: datetime,
        start_unreadable: bool = False,
    ) -> None:
        """Durably open one invocation's boundary BEFORE it launches. Idempotent by its own
        check-then-insert over ``(lease_id, generation, kind)`` (``bzh:sql-portable``) — a
        replayed open for an already-open boundary writes nothing a second time.
        ``start_unreadable=True`` marks a ``start_position is None`` here as a failed read,
        never the fresh-session sentinel."""
        ...

    def record_boundary_advance(
        self,
        *,
        lease_id: str,
        generation: int,
        kind: InvocationBoundaryKind,
        superseded_invocation: str,
        start_position: str | None,
        advanced_at: datetime,
        start_unreadable: bool = False,
    ) -> None:
        """Append one advance past a judge marker's start, keyed by the superseded elicitation's
        identity. Check-then-insert (``bzh:sql-portable``): a replay writes nothing, and an
        unopened boundary is a no-op."""
        ...

    def close_boundaries_for_lease(self, lease_id: str, *, reason: str, at: datetime) -> None:
        """Close every one of this lease's still-open boundaries (``bzh:open-facts-declare-closure``)
        — called once, from the one funnel every lease closure path shares
        (:meth:`~blizzard.runner.loop.attempt.Attempt.close`), so a hub-terminal chunk's
        boundaries close the same way a locally-driven one's do. An UPDATE over ``closed_at
        IS NULL``, naturally idempotent under a crash-and-retry of the closure path itself."""
        ...

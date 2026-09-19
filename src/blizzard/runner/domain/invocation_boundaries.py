"""The durable transcript invocation-boundary repository seam (blizzard#437 D6/D11).

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
]

#: The four invocation kinds a boundary ever names — a nudge's own, distinct from ``resume`` (D5).
InvocationBoundaryKind = Literal["spawn", "resume", "judge", "nudge"]

#: Worker-starting kinds, tried in order (``"judge"`` excluded, D5) — shared with the invariant checker.
WORKER_STARTING_KINDS: tuple[InvocationBoundaryKind, ...] = ("spawn", "resume", "nudge")


@dataclass(frozen=True)
class InvocationBoundaryRecord:
    """One invocation's durable start marker. ``start_position`` is the opaque
    ``TranscriptPosition.token`` minted just before launch, or ``None`` — a fresh session's
    own beginning sentinel. ``start_unreadable`` distinguishes that from a failed tail read;
    ``closed_at``/``closed_reason`` are unset until the owning lease closes."""

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


class IReadInvocationBoundaryRepository(Protocol):
    """Read-only invocation-boundary queries (held by read-path edges)."""

    def boundary(self, lease_id: str, generation: int, kind: InvocationBoundaryKind) -> InvocationBoundaryRecord | None:
        """This exact invocation's boundary, or ``None`` when it was never opened — the
        read interrupted-usage recovery (blizzard#437 Phase 4) keys its range read from."""
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

    def close_boundaries_for_lease(self, lease_id: str, *, reason: str, at: datetime) -> None:
        """Close every one of this lease's still-open boundaries (``bzh:open-facts-declare-closure``,
        D11) — called once, from the one funnel every lease closure path shares
        (:meth:`~blizzard.runner.loop.attempt.Attempt.close`), so a hub-terminal chunk's
        boundaries close the same way a locally-driven one's do. An UPDATE over ``closed_at
        IS NULL``, naturally idempotent under a crash-and-retry of the closure path itself."""
        ...

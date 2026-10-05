"""The durable transcript invocation-boundary repository seam.

One row per fleet-driven invocation — a worker spawn generation, a resume generation, a
judgement, or a nudge — recording the transcript position it started from, durably BEFORE
the invocation launches. Runner-local only: never delivered to the hub, never read outside
the runner plane (``bzh:runner-plane-transcript-reads``)."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime
from typing import Literal, Protocol

from blizzard.foundation.roles import domain_model

__all__ = [
    "BOUNDARY_TRANSITIONS",
    "WORKER_STARTING_KINDS",
    "BoundaryState",
    "BoundaryTransition",
    "IReadInvocationBoundaryRepository",
    "IWriteInvocationBoundaryRepository",
    "InvocationBoundary",
    "InvocationBoundaryKind",
    "InvocationBoundaryStart",
    "boundary_transition_applies",
    "spawn_boundary_kind",
    "worker_boundary_open",
]

#: The four invocation kinds a boundary ever names — a nudge's own, distinct from ``resume``.
InvocationBoundaryKind = Literal["spawn", "resume", "judge", "nudge"]

#: Worker-starting kinds, tried in order (``"judge"`` excluded) — shared with the invariant checker.
WORKER_STARTING_KINDS: tuple[InvocationBoundaryKind, ...] = ("spawn", "resume", "nudge")

#: Where one ``(lease, generation, kind)`` marker stands: never opened, open, or closed.
BoundaryState = Literal["absent", "open", "closed"]

#: The writes a marker ever takes: its one open, an advance past a judge's start, its closure.
BoundaryTransition = Literal["open", "advance", "close"]

#: The transitions that write from each state; a marker opens once ever and a closed marker's history is final.
BOUNDARY_TRANSITIONS: dict[BoundaryState, frozenset[BoundaryTransition]] = {
    "absent": frozenset({"open"}),
    "open": frozenset({"advance", "close"}),
    "closed": frozenset(),
}


@domain_model
@dataclass(frozen=True)
class InvocationBoundary:
    """One invocation's durable start marker — its true start, never rewritten. ``start_position``
    is the opaque ``TranscriptPosition.token`` minted just before launch, or ``None`` — a fresh
    session's own beginning sentinel; ``start_unreadable`` marks a failed tail read instead.
    :data:`BOUNDARY_TRANSITIONS` declares which writes it takes from which state."""

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

    @property
    def state(self) -> BoundaryState:
        """``"closed"`` once its lease's closure stamped it, else ``"open"``."""
        return self._state()

    def _state(self) -> BoundaryState:
        return "closed" if self.closed_at is not None else "open"


def boundary_transition_applies(boundary: InvocationBoundary | None, transition: BoundaryTransition) -> bool:
    """Whether ``transition`` writes over ``boundary`` (``None`` — never opened) per
    :data:`BOUNDARY_TRANSITIONS`; ``False`` is the declared no-op."""
    state: BoundaryState = "absent" if boundary is None else boundary.state
    return transition in BOUNDARY_TRANSITIONS[state]


def worker_boundary_open(boundaries: Iterable[InvocationBoundary], generation: int) -> bool:
    """Whether some worker-starting kind (:data:`WORKER_STARTING_KINDS`) already holds an open
    boundary at ``generation`` — one worker-starting boundary per generation, so a wake never
    opens a second beside a nudge's own."""
    return any(b.generation == generation and b.kind in WORKER_STARTING_KINDS and b.state == "open" for b in boundaries)


def spawn_boundary_kind(*, resumed: bool) -> InvocationBoundaryKind:
    """The boundary kind a spawn opens: ``"resume"`` when it continues an existing session,
    else ``"spawn"`` on the fresh session's beginning sentinel."""
    return "resume" if resumed else "spawn"


@domain_model
@dataclass(frozen=True)
class InvocationBoundaryStart:
    """Where an invocation boundary's range currently starts: the newest advance's own
    values, or the marker's when there is none. ``at`` is when that start was recorded."""

    start_position: str | None
    start_unreadable: bool
    at: datetime


class IReadInvocationBoundaryRepository(Protocol):
    """Read-only invocation-boundary queries (held by read-path edges)."""

    def boundary(self, lease_id: str, generation: int, kind: InvocationBoundaryKind) -> InvocationBoundary | None:
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

    def open_boundaries_for_lease(self, lease_id: str) -> list[InvocationBoundary]:
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
        (:meth:`~blizzard.runner.lifecycle.attempt.Attempt.close`), so a hub-terminal chunk's
        boundaries close the same way a locally-driven one's do. An UPDATE over ``closed_at
        IS NULL``, naturally idempotent under a crash-and-retry of the closure path itself."""
        ...

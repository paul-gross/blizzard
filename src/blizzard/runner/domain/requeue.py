"""The operator requeue — ``blizzard runner requeue <chunk-id>`` (issue #53).

The explicit hand-back after a human worked a needs_human chunk interactively. Appends the
clearing fact only (``bzh:crash-correctness`` — fact first, never a spawn from the edge);
the chunk's route is never released and it never re-enters the hub's queue. The retry
budget is **carried, not reset**: a requeue buys exactly one more try."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING, Protocol

from blizzard.foundation.clock import IClock

if TYPE_CHECKING:
    from blizzard.runner.domain.escalations import EscalationRecord
    from blizzard.runner.domain.takeover import TakeoverRecord

__all__ = [
    "ChunkNotRequeueable",
    "IReadRequeueRepository",
    "IWriteRequeueRepository",
    "RequeueBlockedByOpenTakeover",
    "RequeueScope",
    "RequeueService",
]


@dataclass(frozen=True)
class RequeueScope:
    """The chunk-keyed facts :meth:`RequeueService.requeue` reads, resolved at the edge
    (``bzh:domain-takes-objects``): the runner holds no chunk entity to load, so this
    names exactly the two facts the rule checks rather than an aggregate."""

    chunk_id: str
    open_takeover: TakeoverRecord | None
    open_escalation: EscalationRecord | None


class IReadRequeueRepository(Protocol):
    """Read-only requeue queries (held by read-path edges)."""

    def pending_requeue_chunk_ids(self) -> set[str]:
        """Every chunk id carrying a requeue mark not yet consumed by a later lease mint
        (issue #53).

        The mark is consumed by the next lease mint for the chunk, whose ``created_at``
        lands at or after the requeue."""
        ...


class IWriteRequeueRepository(IReadRequeueRepository, Protocol):
    """Read-write requeue store — held only by the domain."""

    def record_requeue(self, *, chunk_id: str, at: datetime) -> None:
        """Append the clearing fact for a chunk's local needs_human hold (issue #53).

        Recorded before anything else runs (``bzh:crash-correctness``): the fact alone is
        durable the instant this returns, and is read back via
        :meth:`pending_requeue_chunk_ids` — this call never spawns anything itself."""
        ...


class RequeueError(Exception):
    """Base for the requeue domain's refusals — the API edge maps these to ``409``."""


class RequeueBlockedByOpenTakeover(RequeueError):
    """The chunk's takeover is still open — the human's interactive session holds it."""


class ChunkNotRequeueable(RequeueError):
    """The chunk carries no open escalation — nothing needs_human to clear."""


class RequeueService:
    """Composition-root-wired: the requeue store and the clock (issue #53)."""

    def __init__(self, store: IWriteRequeueRepository, clock: IClock) -> None:
        self._store = store
        self._clock = clock

    def requeue(self, scope: RequeueScope) -> None:
        """Clear ``scope.chunk_id``'s local needs_human hold, or raise a ``409``-mapped
        refusal. ``scope`` is already resolved by the caller (``bzh:domain-takes-objects``).

        Checked in order: an **open takeover** refuses first, since a live interactive
        session must end before anything else touches the chunk; then the chunk must carry
        an **open escalation** — the needs_human shape this verb exists to clear."""
        if scope.open_takeover is not None:
            raise RequeueBlockedByOpenTakeover(
                f"chunk {scope.chunk_id} has an open takeover — end the interactive session before requeuing"
            )
        if scope.open_escalation is None:
            raise ChunkNotRequeueable(f"chunk {scope.chunk_id} is not needs_human — nothing to requeue")
        self._store.record_requeue(chunk_id=scope.chunk_id, at=self._clock.now())

"""The operator requeue — ``blizzard runner requeue <chunk-id>``.

The explicit hand-back after a human worked a needs_human chunk interactively. Appends the clearing fact
only (``bzh:crash-correctness``); the route is never released and the retry budget is **carried, not
reset**: a requeue buys exactly one more try. Legal only from :attr:`RequeueStanding.REQUEUEABLE`; a
requeue over a pending one writes a second mark, and the one fresh attempt FILL mints consumes both."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from types import MappingProxyType
from typing import TYPE_CHECKING

from blizzard.foundation.clock import IClock
from blizzard.foundation.roles import domain_model
from blizzard.runner.leases.operator_requests import IWriteRequeueRepository

if TYPE_CHECKING:
    from blizzard.runner.leases.escalations import ParkedEscalation
    from blizzard.runner.lifecycle.takeover import OpenTakeover

__all__ = [
    "REQUEUE_REFUSALS",
    "ChunkNotRequeueable",
    "RequeueBlockedByOpenTakeover",
    "RequeueError",
    "RequeueMark",
    "RequeueScope",
    "RequeueService",
    "RequeueStanding",
    "requeue_mark",
]


class RequeueError(Exception):
    """Base for the requeue domain's refusals."""


class RequeueBlockedByOpenTakeover(RequeueError):
    """The chunk's takeover is still open — the human's interactive session holds it."""


class ChunkNotRequeueable(RequeueError):
    """The chunk carries no open escalation this runner can clear — nothing needs_human,
    or needs_human with no environment held here to spawn the fresh attempt into."""


class RequeueStanding(StrEnum):
    """Where a chunk stands for the requeue verb, in the precedence :attr:`RequeueScope.standing` applies."""

    TAKEN_OVER = "taken-over"
    NOT_NEEDS_HUMAN = "not-needs-human"
    UNHELD = "unheld"
    REQUEUEABLE = "requeueable"


#: The refusal each standing raises for a requeue; ``None`` where the verb is legal.
REQUEUE_REFUSALS: Mapping[RequeueStanding, type[RequeueError] | None] = MappingProxyType(
    {
        RequeueStanding.TAKEN_OVER: RequeueBlockedByOpenTakeover,
        RequeueStanding.NOT_NEEDS_HUMAN: ChunkNotRequeueable,
        RequeueStanding.UNHELD: ChunkNotRequeueable,
        RequeueStanding.REQUEUEABLE: None,
    }
)

_REFUSAL_TEXT: Mapping[RequeueStanding, str] = MappingProxyType(
    {
        RequeueStanding.TAKEN_OVER: (
            "chunk {chunk_id} has an open takeover — end the interactive session before requeuing"
        ),
        RequeueStanding.NOT_NEEDS_HUMAN: "chunk {chunk_id} is not needs_human — nothing to requeue",
        RequeueStanding.UNHELD: "chunk {chunk_id} holds no environment on this runner — requeue it at the hub",
    }
)


@domain_model
@dataclass(frozen=True)
class RequeueScope:
    """The chunk-keyed facts :func:`requeue_mark` reads, resolved at the edge
    (``bzh:domain-takes-objects``): the runner holds no chunk entity to load, so this
    names exactly the facts the rule checks rather than an aggregate."""

    chunk_id: str
    open_takeover: OpenTakeover | None
    open_escalation: ParkedEscalation | None
    held_environment_ids: tuple[str, ...]

    @property
    def standing(self) -> RequeueStanding:
        """An **open takeover** first, since a live interactive session must end before
        anything else touches the chunk; then the chunk must carry an **open escalation**
        — the needs_human shape this verb exists to clear; then it must still hold an
        environment here, or no fresh attempt could ever spawn."""
        return self._standing()

    def _standing(self) -> RequeueStanding:
        if self.open_takeover is not None:
            return RequeueStanding.TAKEN_OVER
        if self.open_escalation is None:
            return RequeueStanding.NOT_NEEDS_HUMAN
        if not self.held_environment_ids:
            return RequeueStanding.UNHELD
        return RequeueStanding.REQUEUEABLE


@domain_model
@dataclass(frozen=True)
class RequeueMark:
    """The clearing fact a legal requeue writes."""

    chunk_id: str
    at: datetime


def requeue_mark(scope: RequeueScope, *, at: datetime) -> RequeueMark:
    """The mark that clears ``scope``'s local needs_human hold at ``at``, or the
    :class:`RequeueError` its :attr:`~RequeueScope.standing` raises."""
    standing = scope.standing
    refusal = REQUEUE_REFUSALS[standing]
    if refusal is not None:
        raise refusal(_REFUSAL_TEXT[standing].format(chunk_id=scope.chunk_id))
    return RequeueMark(chunk_id=scope.chunk_id, at=at)


class RequeueService:
    """Composition-root-wired: the requeue store and the clock."""

    def __init__(self, store: IWriteRequeueRepository, clock: IClock) -> None:
        self._store = store
        self._clock = clock

    def requeue(self, scope: RequeueScope) -> None:
        """Clear ``scope.chunk_id``'s local needs_human hold, or raise the ``409``-mapped
        refusal :func:`requeue_mark` names. ``scope`` is already resolved by the caller
        (``bzh:domain-takes-objects``)."""
        mark = requeue_mark(scope, at=self._clock.now())
        self._store.record_requeue(chunk_id=mark.chunk_id, at=mark.at)

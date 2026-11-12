"""The chunk-escalations repository seam — a chunk parked on
``needs_human``, including one raised by a bounce-cap crossing."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Protocol

from blizzard.foundation.roles import domain_model
from blizzard.hub.domain.artifact.model import StoredArtifact
from blizzard.hub.domain.chunk.model import CloseEnqueueDecision, EscalationOpen
from blizzard.hub.domain.chunk.ports.exclusive import ILockedChunkRead
from blizzard.hub.domain.chunk.ports.fence import Claimant, EpochAdmission, FenceRefusal
from blizzard.hub.domain.kernel.transactions import LockedTransaction
from blizzard.hub.domain.projects.model import ProjectId


class BounceEscalationOutcome(StrEnum):
    """The result of one bounce-cap escalation attempt.

    ``ALREADY_RECORDED`` means an earlier attempt escalated at this epoch, so the chunk is
    escalated all the same; only ``REFUSED`` (the fence turned it away) leaves it unescalated."""

    RECORDED = "recorded"
    ALREADY_RECORDED = "already_recorded"
    REFUSED = "refused"


@domain_model
@dataclass(frozen=True)
class EscalationWrite:
    """What an escalation admitted past the fence wrote: its row id, and the ids of the ``work-item-close-skipped``
    event rows its landing-marker artifacts owed."""

    escalation_id: int
    close_skipped: tuple[int, ...] = ()


class IReadChunkEscalationsRepository(Protocol):
    """Read-only chunk-escalations access."""

    def list_open_escalations(
        self, *, project: ProjectId | None = None, chunk_id: str | None = None
    ) -> list[EscalationOpen]:
        """Open escalations for the project and/or chunk, decided by :meth:`ChunkFacts.open_escalation`."""
        ...


class IWriteChunkEscalationsRepository(IReadChunkEscalationsRepository, Protocol):
    """Read-write chunk-escalations access."""

    def record_escalation(
        self,
        chunk_id: str,
        *,
        epoch: int,
        admission: EpochAdmission,
        terminal: bool,
        claimant: Claimant | None = None,
        takeover_command: str,
        at: datetime,
        decision_id: str | None = None,
        wrapped_takeover_command: str = "",
        cause: str | None,
        detail: str | None,
        artifacts: Sequence[StoredArtifact] = (),
        close_decision: CloseEnqueueDecision | None = None,
        transaction: LockedTransaction,
    ) -> EscalationWrite | FenceRefusal:
        """Record an ``escalation.recorded`` fact (the chunk derives ``needs_human``) behind the write fence
        (``bzh:epoch-fencing``); a refusal writes nothing and is returned. ``decision_id``, when set, closes a gate
        decision no transition or migration will. ``artifacts`` commit or roll back with the escalation row; a
        landing-marker among them owes close intents by ``close_decision``. Joins ``transaction``, a unit that
        locks ``chunk_id`` (``bzh:transaction-participation``)."""
        ...

    def record_bounce_locked(
        self,
        handle: ILockedChunkRead,
        chunk_id: str,
        *,
        epoch: int,
        admission: EpochAdmission,
        terminal: bool,
        cause: str,
        envelope: str,
        at: datetime,
        visit_node_id: str | None = None,
    ) -> bool:
        """Record one delivery kick-back on ``handle``'s already-locked connection (``bzh:store-exclusive-write``),
        idempotent by ``(chunk_id, epoch)`` — narrowed, given ``visit_node_id``, to that node's own exit — and behind
        the write fence (``bzh:epoch-fencing``): a replay, a terminal chunk, or a stale ``epoch`` writes nothing.
        Append-only, and the sole input :meth:`ChunkFacts.bounce_count` derives from. Returns True iff it wrote."""
        ...

    def record_bounce_escalation_locked(
        self,
        handle: ILockedChunkRead,
        chunk_id: str,
        *,
        epoch: int,
        admission: EpochAdmission,
        terminal: bool,
        runner_id: str,
        takeover_command: str,
        at: datetime,
        cause: str,
        detail: str,
    ) -> BounceEscalationOutcome:
        """Escalate a chunk at a hub node on ``handle``'s already-locked connection (``bzh:store-exclusive-write``) —
        committed with the turn's end — idempotent by the escalation's existence at ``epoch``. The hub lease and the
        escalation fact land together; no transition is recorded. Handed the visit's epoch plus one with
        :attr:`EpochAdmission.ABOVE`, so the escalation opens the next visit; behind the write fence
        (``bzh:epoch-fencing``), a terminal chunk or a stale ``epoch`` writes nothing and is ``REFUSED``."""
        ...

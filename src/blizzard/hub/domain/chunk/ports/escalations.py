"""The chunk-escalations repository seam — a chunk parked on
``needs_human``, including one raised by a bounce-cap crossing."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Protocol

from blizzard.hub.domain.chunk.model import EscalationOpen
from blizzard.hub.domain.chunk.ports.exclusive import ILockedChunkRead
from blizzard.hub.domain.chunk.ports.fence import Claimant, EpochAdmission, FenceRefusal
from blizzard.hub.domain.projects.model import ProjectId


class BounceEscalationOutcome(StrEnum):
    """The result of one bounce-cap escalation attempt.

    ``ALREADY_RECORDED`` means an earlier attempt escalated at this epoch, so the chunk is
    escalated all the same; only ``REFUSED`` (the fence turned it away) leaves it unescalated."""

    RECORDED = "recorded"
    ALREADY_RECORDED = "already_recorded"
    REFUSED = "refused"


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
        claimant: Claimant | None = None,
        takeover_command: str,
        at: datetime,
        decision_id: str | None = None,
        wrapped_takeover_command: str = "",
        cause: str | None,
        detail: str | None,
    ) -> int | FenceRefusal:
        """Record an ``escalation.recorded`` fact — the chunk derives ``needs_human``
        until something supersedes it — behind the write fence (``bzh:epoch-fencing``); a
        refusal writes nothing and is returned in place of the row id. The takeover command rides along so the
        parked session is resumable (`blizzard-context:/domain/humans/escalation.md`). ``decision_id``,
        when set, closes a gate decision no transition or migration will."""
        ...

    def record_bounce_locked(
        self,
        handle: ILockedChunkRead,
        chunk_id: str,
        *,
        epoch: int,
        admission: EpochAdmission,
        cause: str,
        envelope: str,
        at: datetime,
    ) -> bool:
        """Record one delivery kick-back on ``handle``'s already-locked connection (``bzh:store-exclusive-write``),
        idempotent by ``(chunk_id, epoch)`` and behind the write fence (``bzh:epoch-fencing``): a replay, a terminal
        chunk, or a stale ``epoch`` writes nothing. Append-only, and the sole input :meth:`ChunkFacts.bounce_count`
        derives from. Returns True iff it wrote."""
        ...

    def record_bounce_escalation_locked(
        self,
        handle: ILockedChunkRead,
        chunk_id: str,
        *,
        epoch: int,
        admission: EpochAdmission,
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

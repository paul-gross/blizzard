"""The chunk-escalations repository seam — a chunk parked on
``needs_human``, including one raised by a bounce-cap crossing."""

from __future__ import annotations

from datetime import datetime
from typing import Protocol

from blizzard.hub.domain.work import EscalationOpen


class IReadChunkEscalationsRepository(Protocol):
    """Read-only chunk-escalations access."""

    def list_open_escalations(self) -> list[EscalationOpen]:
        """Every currently-open escalation, **fleet-wide** (issue #125).

        Each decided by :meth:`ChunkFacts.open_escalation` — the rule's one implementation
        (#293). Low-volume, so the candidate scan is full."""
        ...


class IWriteChunkEscalationsRepository(IReadChunkEscalationsRepository, Protocol):
    """Read-write chunk-escalations access."""

    def record_escalation(
        self,
        chunk_id: str,
        *,
        epoch: int,
        takeover_command: str,
        at: datetime,
        decision_id: str | None = None,
        wrapped_takeover_command: str = "",
    ) -> int:
        """Record an ``escalation.recorded`` fact — the chunk derives ``needs_human``
        until something supersedes it. The takeover command rides along so the
        parked session is resumable (`blizzard-context:/domain/humans/escalation.md`). ``decision_id``,
        when set, closes a gate decision no transition or migration will (issue #110)."""
        ...

    def record_bounce(self, chunk_id: str, *, epoch: int, cause: str, envelope: str, at: datetime) -> bool:
        """Record one delivery kick-back (#64), idempotent by ``(chunk_id, epoch)``.

        Append-only, and the sole input :meth:`ChunkFacts.bounce_count` derives from; the natural key
        makes a redelivery replay after a ``kill -9`` re-enter harmlessly rather than
        double-count. Returns True iff it wrote."""
        ...

    def record_bounce_escalation(
        self, chunk_id: str, *, epoch: int, runner_id: str, takeover_command: str, at: datetime
    ) -> bool:
        """Escalate a chunk whose bounce count crossed its node's cap (#64), atomically and
        idempotently. The hub lease and the escalation fact land in one transaction, guarded
        by the escalation's existence at this epoch. No transition is recorded: the chunk's
        held route and stuck node are untouched. Returns True iff it wrote."""
        ...

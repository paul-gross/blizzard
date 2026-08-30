"""The needs-human escalation repository seam (blizzard#410)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

__all__ = ["EscalationRecord", "IReadEscalationRepository", "IWriteEscalationRepository"]


@dataclass(frozen=True)
class EscalationRecord:
    """A closed-``escalated`` lease not yet superseded — the status view's read (issue #51).

    Open until a later lease is minted for the chunk, or the hub resolves it terminally and
    PULL records an ``escalation_closures`` mark (#292) — two supersessions, no flag."""

    lease_id: str
    chunk_id: str
    node_id: str
    epoch: int
    session_id: str | None
    closed_at: datetime
    session_name: str | None = None
    resolved_model: str | None = None
    resolved_effort: str | None = None


class IReadEscalationRepository(Protocol):
    """Read-only escalation queries (held by read-path edges)."""

    def open_escalations(self) -> list[EscalationRecord]:
        """Every escalated chunk still unsuperseded (issue #51).

        See :class:`EscalationRecord` for what "open" means here."""
        ...

    def open_escalation_for_chunk(self, chunk_id: str) -> EscalationRecord | None:
        """The chunk's open escalation, or ``None`` (issue #53).

        The single-chunk narrowing of :meth:`open_escalations`. Unaffected by a takeover
        in between — a takeover writes neither a closure nor a lease mint."""
        ...


class IWriteEscalationRepository(IReadEscalationRepository, Protocol):
    """Read-write escalation store — held only by the domain."""

    def record_escalation_closure(self, *, chunk_id: str, reason: str, at: datetime) -> None:
        """Mirror the hub having ended a chunk this runner holds an escalation for (#292, #293).

        The supersession no lease mint can supply: a terminal chunk is never claimed again.
        ``reason`` is the hub status observed — ``stopped`` or ``done``."""
        ...

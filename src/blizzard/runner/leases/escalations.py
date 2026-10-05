"""The needs-human escalation repository seam."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from types import MappingProxyType
from typing import TYPE_CHECKING, Protocol

from blizzard.foundation.chunk_status import TERMINAL_STATUSES
from blizzard.foundation.roles import domain_model
from blizzard.runner.environments.repository import EnvBinding
from blizzard.runner.harness.identity import SessionReference

if TYPE_CHECKING:
    from blizzard.wire.chunk import ChunkStatusView

__all__ = [
    "ESCALATION_TRANSITIONS",
    "EscalationState",
    "EscalationVerb",
    "IReadEscalationRepository",
    "IWriteEscalationRepository",
    "ParkedEscalation",
    "resume_workdir",
]


class EscalationState(StrEnum):
    """Where a local escalation stands — open from its escalating closure until superseded."""

    OPEN = "open"
    SUPERSEDED = "superseded"


class EscalationVerb(StrEnum):
    SUPERSEDE = "supersede"
    REQUEUE = "requeue"


#: Verbs legal per state (the store's supersession guard mirrors it); a requeue leaves it open, superseded takes none.
ESCALATION_TRANSITIONS: Mapping[EscalationState, frozenset[EscalationVerb]] = MappingProxyType(
    {
        EscalationState.OPEN: frozenset(EscalationVerb),
        EscalationState.SUPERSEDED: frozenset(),
    }
)


@domain_model
@dataclass(frozen=True)
class ParkedEscalation:
    """A closed-``escalated`` lease not yet superseded — the status view's read.

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
    harness_id: str | None = None
    #: The escalated generation's own recorded harness build version,
    #: read off ``lease_spawns`` beside ``harness_id``. ``None`` when the generation
    #: recorded none.
    harness_version: str | None = None
    cause: str | None = None

    @property
    def session(self) -> SessionReference | None:  # ast-grep-ignore: bzh:property-delegates
        if self.session_id is None:
            return None
        if self.harness_id is None:
            raise ValueError(
                f"escalation on lease {self.lease_id} has session_id {self.session_id!r} but no harness_id"
            )
        return SessionReference(self.harness_id, self.session_id)

    def superseded_by(self, view: ChunkStatusView, *, runner_id: str, fenced_out: bool) -> bool:
        """Whether the hub's ``view`` of this escalation's chunk supersedes it: the chunk
        ended (terminal), the hub no longer routes it to ``runner_id`` (requeued away or
        reassigned), or an operator restart fenced this epoch out (``fenced_out``, judged
        by the caller against the chunk's open takeover)."""
        return view.status in TERMINAL_STATUSES or view.route_runner_id != runner_id or fenced_out


def resume_workdir(session: SessionReference | None, bindings: Sequence[EnvBinding]) -> str | None:
    """The workdir an escalation's resume command lands in — its chunk's first held
    binding — or ``None`` when it can carry no resume command: no session to resume, or no
    environment still held to resume it in."""
    if session is None or not bindings:
        return None
    return bindings[0].workdir


class IReadEscalationRepository(Protocol):
    """Read-only escalation queries (held by read-path edges)."""

    def open_escalations(self) -> list[ParkedEscalation]:
        """Every escalated chunk still unsuperseded.

        See :class:`ParkedEscalation` for what "open" means here."""
        ...

    def open_escalation_for_chunk(self, chunk_id: str) -> ParkedEscalation | None:
        """The chunk's open escalation, or ``None``.

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

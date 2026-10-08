"""The chunk-decisions repository seam — a runner-config gate's open
decision and its first-write-wins resolution."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from blizzard.foundation.roles import domain_model
from blizzard.hub.domain.artifact.model import StoredArtifact
from blizzard.hub.domain.chunk.model import DecisionChoice, GateDecision
from blizzard.hub.domain.chunk.ports.exclusive import ILockedChunkRead
from blizzard.hub.domain.chunk.ports.fence import Claimant, EpochAdmission, FenceRefusal


@domain_model
@dataclass(frozen=True)
class LiveDecisionStatus:
    """A live gate decision's identity and resolution — no choices."""

    decision_id: str
    node_id: str
    epoch: int
    resolved_choice: str | None
    transitioned: bool


class IReadChunkDecisionsRepository(Protocol):
    """Read-only chunk-decisions access."""

    def get_decision(self, decision_id: str) -> GateDecision | None:
        """One gate decision in full, with derived resolution/transition state."""
        ...

    def find_decision(self, chunk_id: str, *, node_id: str, epoch: int) -> GateDecision | None:
        """The decision already open for a (chunk, node, epoch) — the idempotency probe
        for a re-submitted runner-config gate decision (a lost-ack replay)."""
        ...

    def decision_for_chunk(self, chunk_id: str) -> GateDecision | None:
        """The chunk's newest not-yet-transitioned decision."""
        ...

    def list_open_decisions(self) -> list[GateDecision]:
        """Every unresolved decision across the fleet."""
        ...

    def live_decisions_for(self, chunk_ids: Iterable[str]) -> dict[str, LiveDecisionStatus]:
        """Each given chunk's newest not-yet-transitioned decision, lean —
        the by-id-set bulk counterpart to :meth:`decision_for_chunk`, set-based
        throughout. A chunk with no live decision is absent from the dict."""
        ...


class IWriteChunkDecisionsRepository(IReadChunkDecisionsRepository, Protocol):
    """Read-write chunk-decisions access."""

    def record_decision(
        self,
        *,
        decision_id: str,
        chunk_id: str,
        node_id: str,
        node_name: str,
        epoch: int,
        admission: EpochAdmission,
        claimant: Claimant | None = None,
        choices: list[DecisionChoice],
        at: datetime,
        artifacts: list[StoredArtifact],
        imposed_by_runner_id: str | None,
    ) -> FenceRefusal | None:
        """Open a gate decision, committing any step artifacts atomically, behind
        the write fence (``bzh:epoch-fencing``) — a refusal writes nothing and is returned.

        A graph gate passes no artifacts; a runner-config gate carries them
        here, with ``imposed_by_runner_id`` naming the runner — ``None`` for a graph gate."""
        ...

    def record_decision_locked(
        self,
        handle: ILockedChunkRead,
        *,
        decision_id: str,
        chunk_id: str,
        node_id: str,
        node_name: str,
        epoch: int,
        admission: EpochAdmission,
        claimant: Claimant | None = None,
        choices: list[DecisionChoice],
        at: datetime,
        artifacts: list[StoredArtifact],
        imposed_by_runner_id: str | None,
    ) -> bool | FenceRefusal:
        """:meth:`record_decision` on ``handle``'s already-locked connection
        (``bzh:store-exclusive-write``), so the caller's guard reads and this write share one
        lock. A decision already open at ``(chunk_id, node_id, epoch)`` answers ``False`` — the
        lost-ack replay, nothing written; ``True`` is a fresh write; a refusal writes nothing."""
        ...

    def record_decision_resolution(self, decision_id: str, *, choice: str, resolved_by: str, at: datetime) -> bool:
        """First-write-wins CAS: record the person's choice, or return ``False`` if the
        decision was already resolved (the loser is told who won)."""
        ...

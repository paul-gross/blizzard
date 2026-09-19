"""The chunk-decisions repository seam — a runner-config gate's open
decision and its first-write-wins resolution."""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from blizzard.hub.domain.artifacts import ArtifactRow
from blizzard.hub.domain.proposals import WorkItemProposalRow
from blizzard.hub.domain.work import DecisionChoice, DecisionRow, DocketEntry


@dataclass(frozen=True)
class LiveDecisionStatus:
    """A live gate decision's identity and resolution (blizzard#521) — no choices, no
    docket."""

    decision_id: str
    node_id: str
    epoch: int
    resolved_choice: str | None
    transitioned: bool


class IReadChunkDecisionsRepository(Protocol):
    """Read-only chunk-decisions access."""

    def get_decision(self, decision_id: str) -> DecisionRow | None:
        """One gate decision in full, with derived resolution/transition state."""
        ...

    def find_decision(self, chunk_id: str, *, node_id: str, epoch: int) -> DecisionRow | None:
        """The decision already open for a (chunk, node, epoch) — the idempotency probe
        for a re-submitted runner-config gate decision (a lost-ack replay)."""
        ...

    def decision_for_chunk(self, chunk_id: str) -> DecisionRow | None:
        """The chunk's newest not-yet-transitioned decision."""
        ...

    def list_open_decisions(self) -> list[DecisionRow]:
        """Every unresolved decision across the fleet."""
        ...

    def dockets_for_chunks(self, chunk_ids: Sequence[str]) -> dict[str, list[DocketEntry]]:
        """Every requested chunk's docket, keyed by chunk id, as ``decision_for_chunk``
        would carry it — a chunk with no pending proposals maps to an empty list, never
        an absent key."""
        ...

    def live_decisions_for(self, chunk_ids: Iterable[str]) -> dict[str, LiveDecisionStatus]:
        """Each given chunk's newest not-yet-transitioned decision, lean (blizzard#521) —
        the by-id-set bulk counterpart to :meth:`decision_for_chunk`, set-based
        throughout rather than :meth:`get_decision`'s per-decision docket/choices work.
        A chunk with no live decision is absent from the dict."""
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
        choices: list[DecisionChoice],
        at: datetime,
        artifacts: list[ArtifactRow],
        proposals: list[WorkItemProposalRow],
    ) -> None:
        """Open a gate decision, committing any step artifacts and proposals atomically.

        A graph gate passes neither (they landed with the arriving transition); a
        runner-config gate carries the gated step's artifacts and proposals here, exactly
        where the step's transition would have written them."""
        ...

    def record_decision_resolution(
        self, decision_id: str, *, choice: str, resolved_by: str, at: datetime, struck: Sequence[str] = ()
    ) -> bool:
        """First-write-wins CAS: record the person's choice and ``struck``'s proposal
        ids as a strike each, in one transaction, or return ``False`` if the decision
        was already resolved (the loser is told who won — and writes no strike at all)."""
        ...

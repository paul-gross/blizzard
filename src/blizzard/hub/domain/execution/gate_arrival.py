"""A graph gate opens one decision for each landed (chunk, node, epoch)."""

from blizzard.foundation.clock import IClock
from blizzard.foundation.ids import Id, IdPrefix
from blizzard.hub.domain.chunk.model import CloseEnqueueDecision
from blizzard.hub.domain.chunk.ports.decisions import IWriteChunkDecisionsRepository
from blizzard.hub.domain.chunk.ports.exclusive import ILockedChunkRead
from blizzard.hub.domain.chunk.ports.facts import IReadChunkFactsRepository
from blizzard.hub.domain.chunk.ports.fence import Claimant, EpochAdmission, FenceRefusal
from blizzard.hub.domain.chunk.terminal import terminal_under_lock
from blizzard.hub.domain.execution.completion import NextStep, NextStepKind, decision_choices
from blizzard.hub.domain.graph.model import Graph


def open_graph_gate_on_arrival(
    decisions: IWriteChunkDecisionsRepository,
    facts: IReadChunkFactsRepository,
    handle: ILockedChunkRead,
    clock: IClock,
    *,
    chunk_id: str,
    graph: Graph,
    node_id: str,
    epoch: int,
    close_decision: CloseEnqueueDecision,
    claimant: Claimant | None = None,
) -> tuple[int, ...]:
    """Join the landing write's lock and transaction; hub-owned restarts have no claimant. Answers the ids of the
    ``work-item-close-skipped`` event rows the decision wrote, for the unit's owner to publish once it commits.
    The decision's terminal verdict is read fresh on ``handle``, after the movement this unit already wrote
    (``bzh:write-reestablishes-read``)."""
    step = NextStep.of(graph, node_id)
    if step.kind is not NextStepKind.GATE or step.node is None:
        return ()
    result = decisions.record_decision_locked(
        handle,
        decision_id=Id.mint(IdPrefix.DECISION, clock).value,
        chunk_id=chunk_id,
        node_id=step.node.node_id,
        node_name=step.node.name,
        epoch=epoch,
        admission=EpochAdmission.CURRENT,
        terminal=terminal_under_lock(facts, chunk_id, transaction=handle.transaction),
        claimant=claimant,
        choices=decision_choices(step.node),
        at=clock.now(),
        artifacts=[],
        imposed_by_runner_id=None,
        close_decision=close_decision,
    )
    if isinstance(result, FenceRefusal):
        raise RuntimeError(f"gate arrival refused after movement: {result.detail}")
    return result.close_skipped

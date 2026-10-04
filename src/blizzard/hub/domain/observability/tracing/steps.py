"""Step identification and closing — which steps a chunk's facts hold, and how each ended.

Contract: ``blizzard-product:/delivered/tracing/fleet-spans/spec/spans.md`` §Steps and §Closing a step.
Pure: a :class:`StepFacts` in, an ordered tuple of :class:`NodeStep` out."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum

from blizzard.foundation.migration_source import MigrationSource
from blizzard.foundation.roles import domain_model
from blizzard.foundation.trace_ids import StepKey
from blizzard.hub.domain.observability.tracing.facts import StepFacts, TracedDecision
from blizzard.hub.domain.observability.tracing.position import (
    Position,
    movement_arrivals,
    position_at,
    position_of_node,
)


class StepKind(StrEnum):
    RUNNER = "runner"
    HUB = "hub"
    GATE = "gate"


class StepOutcome(StrEnum):
    """``blizzard.step.outcome``."""

    TRANSITIONED = "transitioned"
    GATED = "gated"
    MIGRATED = "migrated"
    ESCALATED = "escalated"
    RELEASED = "released"
    STOPPED = "stopped"
    COMPLETED = "completed"
    SUPERSEDED = "superseded"
    DECIDED = "decided"
    RESTARTED = "restarted"


class PrecededBy(StrEnum):
    """``blizzard.step.preceded_by`` — what sits between this step and the last."""

    RESTART = "restart"
    REQUEUE = "requeue"
    RELEASED_CLAIM = "released-claim"


@domain_model
@dataclass(frozen=True)
class StepClose:
    """How and when a step ended; ``fact`` is the closing fact record itself."""

    outcome: StepOutcome
    at: datetime
    fact: object


@domain_model
@dataclass(frozen=True)
class NodeStep:
    """One identified step. ``close`` is ``None`` while the step is open.

    ``resolved_at`` is a gate's own moment of decision, kept apart from ``close.at`` — the closing
    fact lands when the holding runner picks the decision up, possibly long after."""

    kind: StepKind
    key: StepKey
    epoch: int
    runner_id: str | None
    start: datetime
    position: Position
    close: StepClose | None = None
    decision_id: str | None = None
    resolved_at: datetime | None = None
    preceded_by: PrecededBy | None = None


@domain_model
@dataclass(frozen=True)
class _Candidate:
    at: datetime
    order: int
    outcome: StepOutcome
    fact: object

    def rank(self) -> tuple[datetime, int]:
        return (self.at, self.order)


def _earliest(candidates: list[_Candidate]) -> StepClose | None:
    if not candidates:
        return None
    best = min(candidates, key=_Candidate.rank)
    return StepClose(best.outcome, best.at, best.fact)


def _runner_close(facts: StepFacts, epoch: int, start: datetime) -> StepClose | None:
    """A runner or hub step closes at the earliest closing fact; table order breaks a tie."""
    found: list[_Candidate] = []
    here = [t.recorded_at for t in facts.transitions if t.epoch == epoch]
    here += [d.submitted_at for d in facts.decisions if d.epoch == epoch]
    here += [m.recorded_at for m in facts.migrations if m.epoch == epoch]
    here += [e.recorded_at for e in facts.escalations if e.epoch == epoch]
    here += [r.recorded_at for r in facts.restarts if r.epoch == epoch]
    for transition in facts.transitions:
        if transition.epoch == epoch and transition.decision_id is None:
            found.append(_Candidate(transition.recorded_at, 0, StepOutcome.TRANSITIONED, transition))
    for decision in facts.decisions:
        if decision.epoch == epoch and decision.imposed_by_runner_id is not None:
            found.append(_Candidate(decision.submitted_at, 1, StepOutcome.GATED, decision))
    for migration in facts.migrations:
        if migration.epoch == epoch and migration.source is not MigrationSource.RESTART:
            found.append(_Candidate(migration.recorded_at, 2, StepOutcome.MIGRATED, migration))
    for escalation in facts.escalations:
        if escalation.epoch == epoch:
            found.append(_Candidate(escalation.recorded_at, 3, StepOutcome.ESCALATED, escalation))
    for released in facts.route_released:
        if released.released_at > start and not any(at > released.released_at for at in here):
            found.append(_Candidate(released.released_at, 4, StepOutcome.RELEASED, released))
    found += _terminal_candidates(facts, start, epoch)
    return _earliest(found)


def _terminal_candidates(facts: StepFacts, start: datetime, epoch: int) -> list[_Candidate]:
    found: list[_Candidate] = []
    for stopped in facts.chunk_stopped:
        if stopped.recorded_at > start:
            found.append(_Candidate(stopped.recorded_at, 5, StepOutcome.STOPPED, stopped))
    for completed in facts.chunk_completed:
        if completed.recorded_at > start:
            found.append(_Candidate(completed.recorded_at, 6, StepOutcome.COMPLETED, completed))
    higher = [o for o in facts.epoch_owners if o.epoch > epoch and o.recorded_at >= start]
    if higher:
        first = min(higher, key=lambda o: o.recorded_at)
        found.append(_Candidate(first.recorded_at, 7, StepOutcome.SUPERSEDED, first))
    return found


def _gate_close(facts: StepFacts, decision: TracedDecision) -> StepClose | None:
    """A gate closes at the first fact carrying its ``decision_id``, from the four closing tables."""
    key = decision.decision_id
    found: list[_Candidate] = []
    found += [_Candidate(t.recorded_at, 0, StepOutcome.DECIDED, t) for t in facts.transitions if t.decision_id == key]
    found += [_Candidate(m.recorded_at, 1, StepOutcome.MIGRATED, m) for m in facts.migrations if m.decision_id == key]
    found += [_Candidate(e.recorded_at, 2, StepOutcome.ESCALATED, e) for e in facts.escalations if e.decision_id == key]
    found += [_Candidate(r.recorded_at, 3, StepOutcome.RESTARTED, r) for r in facts.restarts if r.decision_id == key]
    stand = _terminal_candidates(facts, decision.submitted_at, decision.epoch)
    return _earliest(found + stand)


def _hub_start(facts: StepFacts, minted_at: datetime) -> datetime:
    """The latest fact that placed the chunk on the hub node before the step's exit."""
    placed = [a for a in movement_arrivals(facts) if a.recorded_at < minted_at]
    if not placed:
        return minted_at
    start = placed[-1].recorded_at
    later = [r.requeued_at for r in facts.requeues if start < r.requeued_at <= minted_at]
    return max([start, *later])


def _attempt_steps(facts: StepFacts) -> list[NodeStep]:
    steps: list[NodeStep] = []
    owners = {o.epoch: o for o in sorted(facts.epoch_owners, key=lambda o: o.recorded_at, reverse=True)}
    for epoch in sorted({lease.epoch for lease in facts.lease_facts}):
        owner = owners.get(epoch)
        if owner is None:
            continue
        minted = min(lease.minted_at for lease in facts.lease_facts if lease.epoch == epoch)
        hub = owner.runner_id is None
        start = _hub_start(facts, minted) if hub else minted
        steps.append(
            NodeStep(
                kind=StepKind.HUB if hub else StepKind.RUNNER,
                key=StepKey.attempt(facts.chunk_id, epoch),
                epoch=epoch,
                runner_id=owner.runner_id,
                start=start,
                position=position_at(facts, start),
                close=_runner_close(facts, epoch, start),
            )
        )
    return steps


def _gate_steps(facts: StepFacts) -> list[NodeStep]:
    resolved = {r.decision_id: r.resolved_at for r in facts.decision_resolutions}
    return [
        NodeStep(
            kind=StepKind.GATE,
            key=StepKey.gate(facts.chunk_id, d.epoch, d.decision_id),
            epoch=d.epoch,
            runner_id=d.imposed_by_runner_id,
            start=d.submitted_at,
            position=position_of_node(facts, d.node_id, d.submitted_at),
            close=_gate_close(facts, d),
            decision_id=d.decision_id,
            resolved_at=resolved.get(d.decision_id),
        )
        for d in facts.decisions
    ]


def _fence_events(facts: StepFacts, step_epochs: set[int]) -> list[tuple[datetime, PrecededBy]]:
    """Fence-only events: an owner row nothing stands behind, and requeues."""
    gate_epochs = {d.epoch for d in facts.decisions}
    restart_epochs = {r.epoch for r in facts.restarts}
    restart_epochs |= {m.epoch for m in facts.migrations if m.source is MigrationSource.RESTART}
    events: list[tuple[datetime, PrecededBy]] = []
    for owner in facts.epoch_owners:
        if owner.epoch in step_epochs or owner.epoch in gate_epochs:
            continue
        kind = PrecededBy.RESTART if owner.epoch in restart_epochs else PrecededBy.RELEASED_CLAIM
        events.append((owner.recorded_at, kind))
    events += [(r.requeued_at, PrecededBy.REQUEUE) for r in facts.requeues]
    return sorted(events, key=lambda e: e[0])


def _with_preceded_by(facts: StepFacts, steps: list[NodeStep]) -> tuple[NodeStep, ...]:
    events = _fence_events(facts, {s.epoch for s in steps if s.kind is not StepKind.GATE})
    out: list[NodeStep] = []
    previous: NodeStep | None = None
    for step in steps:
        low = previous.start if previous is not None else None
        between = [kind for at, kind in events if (low is None or at > low) and at < step.start]
        out.append(_replace_preceded(step, between[-1] if between else None))
        previous = step
    return tuple(out)


def _replace_preceded(step: NodeStep, preceded_by: PrecededBy | None) -> NodeStep:
    return NodeStep(
        kind=step.kind,
        key=step.key,
        epoch=step.epoch,
        runner_id=step.runner_id,
        start=step.start,
        position=step.position,
        close=step.close,
        decision_id=step.decision_id,
        resolved_at=step.resolved_at,
        preceded_by=preceded_by,
    )


def identify_steps(facts: StepFacts) -> tuple[NodeStep, ...]:
    """Every runner, hub and gate step the facts hold, ordered by start (epoch, then attempt before gate)."""
    steps = _attempt_steps(facts) + _gate_steps(facts)
    steps.sort(key=lambda s: (s.start, s.epoch, s.kind is StepKind.GATE))
    return _with_preceded_by(facts, steps)

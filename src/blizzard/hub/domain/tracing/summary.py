"""The step summary — every dimension and measure of one closed step, computed once.

Contract: ``blizzard-product:/delivered/tracing/fleet-spans/spec/spans.md`` §Identity. Pure and OpenTelemetry-free: a
:class:`StepFacts` and a closed :class:`NodeStep` in, one :class:`StepSummary` out. A span tree and an egress row are
both mappings of it, so neither can disagree with the other about outcome, waits, asks or totals."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum

from blizzard.foundation.trace_ids import StepKey
from blizzard.hub.domain.graph import RESERVED_TERMINAL
from blizzard.hub.domain.tracing.facts import (
    MigrationRecord,
    PauseRecord,
    QuestionRecord,
    RestartRecord,
    StepFacts,
    TransitionRecord,
)
from blizzard.hub.domain.tracing.steps import NodeStep, PrecededBy, StepKind, StepOutcome, identify_steps
from blizzard.hub.domain.work import MigrationSource, UsageFact, UsageTotal

_HUMAN = "human"


class IntervalKind(StrEnum):
    """The kinds of source interval a step carries; every kind but :attr:`HUB_EXEC` is a wait."""

    QUEUE = "queue"
    CLAIM = "claim"
    ASK = "ask"
    PAUSE = "pause"
    PICKUP = "pickup"
    HUB_EXEC = "hub-exec"


@dataclass(frozen=True)
class Interval:
    """One stretch of a step's time. ``answered`` is read only on an ask; ``clock_skew`` marks a clamped one."""

    kind: IntervalKind
    discriminator: str
    start: datetime
    end: datetime
    answered: bool | None = None
    clock_skew: bool = False

    def ms(self) -> int:
        return _ms(self.start, self.end)


@dataclass(frozen=True)
class StepSummary:
    """One closed step. ``runner_id`` is the holder as a span reports it — a gate's holding runner included."""

    step_key: StepKey
    kind: StepKind
    chunk_id: str
    work_refs: tuple[str, ...]
    work_sources: tuple[str, ...]
    graph_id: str
    graph_name: str
    node_id: str
    node_name: str
    node_executor: str
    epoch: int
    decision_id: str | None
    visit: int
    runner_id: str | None
    harness_id: str | None
    models: tuple[str, ...]
    started_at: datetime
    ended_at: datetime
    closed_at: datetime
    outcome: StepOutcome
    choice: str | None
    to_node_name: str | None
    preceded_by: PrecededBy | None
    bounce_cause: str | None
    asks: int
    asks_unanswered: int
    wait_queue_ms: int
    wait_claim_ms: int
    wait_ask_ms: int
    wait_pause_ms: int
    wait_pickup_ms: int
    invocations: int
    input_tokens: int
    output_tokens: int
    cache_read_tokens: int
    cache_create_tokens: int
    #: Billed and estimated cost, each ``None`` when no invocation carried one.
    cost_billed_usd: float | None
    cost_estimated_usd: float | None
    cost_partial: bool
    billed_partial: bool
    intervals: tuple[Interval, ...]
    #: The step's invocations, oldest first.
    usage: tuple[UsageFact, ...]

    def folded_cost_usd(self) -> float:
        """Billed plus estimated, ``None`` read as zero — the span root's ``blizzard.step.cost.usd``."""
        return folded_cost(self.cost_billed_usd, self.cost_estimated_usd)


def folded_cost(billed: float | None, estimated: float | None) -> float:
    return (billed or 0.0) + (estimated or 0.0)


def invocation_cost_usd(row: UsageFact) -> float:
    """One invocation's folded cost, by the same fold as a step's."""
    total = UsageTotal.of([row])
    return folded_cost(total.billed_cost_usd, total.estimated_cost_usd)


def _ms(start: datetime, end: datetime) -> int:
    return max(0, round((end - start).total_seconds() * 1000))


def step_end(step: NodeStep) -> datetime:
    """A step's end: the closing fact, or a gate's own moment of decision; never before its start."""
    assert step.close is not None
    end = step.close.at
    if step.kind is StepKind.GATE and step.resolved_at is not None:
        end = step.resolved_at
    return max(end, step.start)


def _node_executor(facts: StepFacts, step: NodeStep) -> str:
    if step.kind is StepKind.GATE:
        return _HUMAN
    graph = facts.graphs.get(step.position.graph_id)
    node = graph.node_by_id(step.position.node_id) if graph is not None else None
    if node is not None:
        return node.executor.value
    return "hub" if step.kind is StepKind.HUB else "runner"


def _runner_id(facts: StepFacts, step: NodeStep) -> str | None:
    if step.kind is not StepKind.GATE or step.runner_id is not None:
        return step.runner_id
    owners = [o for o in facts.epoch_owners if o.epoch == step.epoch]
    return max(owners, key=lambda o: o.recorded_at).runner_id if owners else None


def _node_name_of(facts: StepFacts, graph_id: str, node_id: str) -> str | None:
    if node_id == RESERVED_TERMINAL:
        return RESERVED_TERMINAL
    graph = facts.graphs.get(graph_id)
    node = graph.node_by_id(node_id) if graph is not None else None
    return node.name if node is not None else None


def _led_to(facts: StepFacts, fact: object) -> tuple[str | None, str | None]:
    """``(to_node.name, choice)`` read from the closing fact."""
    if isinstance(fact, TransitionRecord):
        return _node_name_of(facts, fact.graph_id, fact.to_node_id), fact.choice_name
    if isinstance(fact, MigrationRecord):
        graph = facts.graphs.get(fact.to_graph_id)
        return (f"graph:{graph.name}" if graph is not None else None), fact.choice_name
    if isinstance(fact, RestartRecord):
        return _node_name_of(facts, fact.graph_id, fact.to_node_id), None
    return None, None


def _usage_of(facts: StepFacts, step: NodeStep) -> tuple[UsageFact, ...]:
    if step.kind is StepKind.GATE:
        return ()
    return tuple(sorted((u for u in facts.usage if u.epoch == step.epoch), key=lambda u: u.recorded_at))


def _bounce_cause(facts: StepFacts, step: NodeStep) -> str | None:
    if step.kind is StepKind.GATE:
        return None
    return next((b.cause for b in facts.bounces if b.epoch == step.epoch), None)


def _choice_and_destination(facts: StepFacts, step: NodeStep) -> tuple[str | None, str | None]:
    assert step.close is not None
    to_node, choice = _led_to(facts, step.close.fact)
    resolution = next((r for r in facts.decision_resolutions if r.decision_id == step.decision_id), None)
    if step.kind is StepKind.GATE and resolution is not None and resolution.choice is not None:
        choice = resolution.choice
    return choice, to_node


def _claimable_at(facts: StepFacts, before: datetime) -> datetime:
    """The latest instant at or before ``before`` the chunk became claimable."""
    instants = [p.promoted_at for p in facts.promotions]
    instants += [r.released_at for r in facts.route_released]
    instants += [r.requeued_at for r in facts.requeues]
    instants += [p.set_at for p in facts.pauses if not p.paused]
    instants += [m.met_at for m in facts.prerequisites_met]
    instants += [r.recorded_at for r in facts.restarts]
    instants += [m.recorded_at for m in facts.migrations if m.source is MigrationSource.RESTART]
    eligible = [at for at in instants if at <= before]
    return max(eligible, default=before)


def _claim_intervals(facts: StepFacts, step: NodeStep, steps: tuple[NodeStep, ...]) -> list[Interval]:
    index = next((i for i, s in enumerate(steps) if s.key == step.key), None)
    floor = steps[index - 1].start if index else None
    created = [
        r.created_at
        for r in facts.routes_created
        if r.created_at <= step.start and (floor is None or r.created_at > floor)
    ]
    if not created:
        return []
    claim_at = max(created)
    intervals = [Interval(IntervalKind.QUEUE, "", min(_claimable_at(facts, claim_at), claim_at), claim_at)]
    if step.kind is StepKind.RUNNER:
        intervals.append(Interval(IntervalKind.CLAIM, "", claim_at, step.start))
    return intervals


def _ask_interval(question: QuestionRecord, end: datetime) -> Interval:
    finish = question.answered_at if question.answered_at is not None else end
    skewed = finish < question.asked_at
    return Interval(
        IntervalKind.ASK,
        question.question_id,
        question.asked_at,
        question.asked_at if skewed else finish,
        answered=question.answered_at is not None,
        clock_skew=skewed,
    )


def _pause_intervals(pauses: tuple[PauseRecord, ...], start: datetime, end: datetime) -> list[Interval]:
    ordered = sorted(pauses, key=lambda p: p.set_at)
    intervals: list[Interval] = []
    for pause in ordered:
        if not pause.paused or not start <= pause.set_at <= end:
            continue
        lift = next((p.set_at for p in ordered if not p.paused and p.set_at > pause.set_at), end)
        intervals.append(
            Interval(IntervalKind.PAUSE, pause.id, pause.set_at, min(max(lift, pause.set_at), max(end, pause.set_at)))
        )
    return intervals


def _intervals(facts: StepFacts, step: NodeStep, steps: tuple[NodeStep, ...], end: datetime) -> list[Interval]:
    assert step.close is not None
    if step.kind is StepKind.GATE:
        if step.resolved_at is None:
            return []
        return [Interval(IntervalKind.PICKUP, "", step.resolved_at, max(step.close.at, step.resolved_at))]
    intervals = _claim_intervals(facts, step, steps)
    intervals += [_ask_interval(q, end) for q in facts.questions if q.epoch == step.epoch]
    intervals += _pause_intervals(facts.pauses, step.start, end)
    if step.kind is StepKind.HUB:
        node_id = step.position.node_id
        for slot in facts.hub_exec_slots:
            if slot.node_id != node_id or not step.start <= slot.acquired_at <= end:
                continue
            released = min(slot.released_at, end) if slot.released_at is not None else end
            intervals.append(
                Interval(IntervalKind.HUB_EXEC, slot.slot_id, slot.acquired_at, max(released, slot.acquired_at))
            )
    return intervals


def summarize_step(facts: StepFacts, step: NodeStep, steps: tuple[NodeStep, ...] | None = None) -> StepSummary:
    """The summary of one closed step. An open step is refused: only closed steps are summarized.

    ``steps`` is the chunk's identified steps when the caller already holds them."""
    if step.close is None:
        raise ValueError(f"step {step.key.text()} is open; only closed steps are summarized")
    steps = identify_steps(facts) if steps is None else steps
    end = step_end(step)
    usage = _usage_of(facts, step)
    total = UsageTotal.of(list(usage))
    intervals = _intervals(facts, step, steps, end)
    waited = {kind: sum(i.ms() for i in intervals if i.kind is kind) for kind in IntervalKind}
    asks = [i for i in intervals if i.kind is IntervalKind.ASK]
    choice, to_node = _choice_and_destination(facts, step)
    graph = facts.graphs.get(step.position.graph_id)
    return StepSummary(
        step_key=step.key,
        kind=step.kind,
        chunk_id=facts.chunk_id,
        work_refs=facts.work_refs,
        work_sources=facts.work_sources,
        graph_id=step.position.graph_id,
        graph_name=graph.name if graph is not None else step.position.graph_id,
        node_id=step.position.node_id,
        node_name=step.position.node_name,
        node_executor=_node_executor(facts, step),
        epoch=step.epoch,
        decision_id=step.decision_id,
        visit=step.position.visit,
        runner_id=_runner_id(facts, step),
        harness_id=next((u.harness_id for u in reversed(usage) if u.harness_id is not None), None),
        models=tuple(dict.fromkeys(u.model for u in usage)),
        started_at=step.start,
        ended_at=end,
        closed_at=step.close.at,
        outcome=step.close.outcome,
        choice=choice,
        to_node_name=to_node,
        preceded_by=step.preceded_by,
        bounce_cause=_bounce_cause(facts, step),
        asks=len(asks),
        asks_unanswered=sum(1 for i in asks if not i.answered),
        wait_queue_ms=waited[IntervalKind.QUEUE],
        wait_claim_ms=waited[IntervalKind.CLAIM],
        wait_ask_ms=waited[IntervalKind.ASK],
        wait_pause_ms=waited[IntervalKind.PAUSE],
        wait_pickup_ms=waited[IntervalKind.PICKUP],
        invocations=len(usage),
        input_tokens=total.input_tokens,
        output_tokens=total.output_tokens,
        cache_read_tokens=total.cache_read_tokens,
        cache_create_tokens=total.cache_create_tokens,
        cost_billed_usd=total.billed_cost_usd,
        cost_estimated_usd=total.estimated_cost_usd,
        cost_partial=total.cost_partial,
        billed_partial=total.billed_partial,
        intervals=tuple(intervals),
        usage=usage,
    )

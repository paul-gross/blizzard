"""Assembling one closed step into its finished span records.

Contract: ``blizzard-product:/delivered/tracing/fleet-spans/spec/spans.md`` §Spans in a step's trace, §Span events,
§Links, §Status, §Attributes, §GenAI usage and §What never leaves. Pure: a :class:`StepFacts` and a closed
:class:`NodeStep` in, an ordered tuple of :class:`SpanRecord` out — root first — with no clock, env read or I/O."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from blizzard.foundation import trace_attributes as shared
from blizzard.foundation.trace_ids import DerivedContext, SpanRole, step_root
from blizzard.foundation.trace_spans import (
    Attributes,
    AttributeValue,
    EventRecord,
    LinkRecord,
    SpanRecord,
    SpanStatus,
)
from blizzard.hub.domain.graph import RESERVED_TERMINAL
from blizzard.hub.domain.tracing import attributes as attr
from blizzard.hub.domain.tracing.facts import (
    MigrationRecord,
    PauseRecord,
    QuestionRecord,
    RestartRecord,
    StepFacts,
    TransitionRecord,
)
from blizzard.hub.domain.tracing.steps import NodeStep, PrecededBy, StepKind, StepOutcome, identify_steps
from blizzard.hub.domain.work import UsageFact, UsageTotal

_MOVEMENTS = frozenset({StepOutcome.TRANSITIONED, StepOutcome.MIGRATED, StepOutcome.DECIDED, StepOutcome.RESTARTED})
_HUMAN = "human"


@dataclass(frozen=True)
class _Child:
    role: SpanRole
    discriminator: str
    name: str
    start: datetime
    end: datetime
    extra: Attributes
    wait: str | None = None


def _ms(start: datetime, end: datetime) -> int:
    return max(0, round((end - start).total_seconds() * 1000))


def _end_of(step: NodeStep) -> datetime:
    """A root's end: the closing fact, or a gate's own moment of decision; never before its start."""
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


def _usage_of(facts: StepFacts, step: NodeStep) -> list[UsageFact]:
    if step.kind is StepKind.GATE:
        return []
    return sorted((u for u in facts.usage if u.epoch == step.epoch), key=lambda u: u.recorded_at)


def _bounce_cause(facts: StepFacts, step: NodeStep) -> str | None:
    if step.kind is StepKind.GATE:
        return None
    return next((b.cause for b in facts.bounces if b.epoch == step.epoch), None)


def _dimensions(facts: StepFacts, step: NodeStep, usage: list[UsageFact]) -> dict[str, AttributeValue]:
    assert step.close is not None
    graph = facts.graphs[step.position.graph_id]
    dims: dict[str, AttributeValue] = {
        shared.CHUNK_ID: facts.chunk_id,
        shared.GRAPH_NAME: graph.name,
        shared.GRAPH_ID: graph.graph_id,
        shared.NODE_NAME: step.position.node_name,
        shared.NODE_ID: step.position.node_id,
        shared.NODE_EXECUTOR: _node_executor(facts, step),
        shared.STEP_EPOCH: step.epoch,
        shared.STEP_VISIT: step.position.visit,
        attr.STEP_OUTCOME: step.close.outcome.value,
    }
    if facts.work_refs:
        dims[shared.CHUNK_WORK_REFS] = tuple(facts.work_refs)
    to_node, choice = _led_to(facts, step.close.fact)
    resolution = next((r for r in facts.decision_resolutions if r.decision_id == step.decision_id), None)
    if step.kind is StepKind.GATE and resolution is not None and resolution.choice is not None:
        choice = resolution.choice
    optional: dict[str, str | None] = {
        attr.STEP_CHOICE: choice,
        attr.STEP_TO_NODE_NAME: to_node,
        attr.STEP_PRECEDED_BY: step.preceded_by.value if step.preceded_by is not None else None,
        attr.RUNNER_ID: _runner_id(facts, step),
        shared.HARNESS_ID: next((u.harness_id for u in reversed(usage) if u.harness_id is not None), None),
        attr.BOUNCE_CAUSE: _bounce_cause(facts, step),
    }
    dims.update({k: v for k, v in optional.items() if v is not None})
    if usage:
        dims[attr.STEP_MODELS] = tuple(dict.fromkeys(u.model for u in usage))
    return dims


def _cost(total: UsageTotal) -> float:
    return total.cost_usd + (total.estimated_cost_usd or 0.0)


def _measures(usage: list[UsageFact], waits: dict[str, int]) -> dict[str, AttributeValue]:
    total = UsageTotal.of(usage)
    return {
        attr.STEP_INPUT_TOKENS: total.input_tokens,
        attr.STEP_OUTPUT_TOKENS: total.output_tokens,
        attr.STEP_CACHE_READ_TOKENS: total.cache_read_tokens,
        attr.STEP_CACHE_CREATE_TOKENS: total.cache_create_tokens,
        attr.STEP_COST_USD: _cost(total),
        attr.STEP_COST_ESTIMATED: total.estimated_cost_usd is not None,
        attr.STEP_COST_PARTIAL: total.cost_partial,
        **waits,
    }


def _invocation(row: UsageFact, at: datetime) -> EventRecord:
    total = UsageTotal.of([row])
    attrs: dict[str, AttributeValue] = {
        shared.INVOCATION_KIND: row.kind,
        shared.GEN_AI_RESPONSE_MODEL: row.model,
        **shared.genai_usage(
            input_tokens=row.input_tokens,
            output_tokens=row.output_tokens,
            cache_read_tokens=row.cache_read_tokens,
            cache_create_tokens=row.cache_create_tokens,
        ),
    }
    if row.harness_id is not None:
        attrs[shared.HARNESS_ID] = row.harness_id
    if row.harness_version is not None:
        attrs[shared.HARNESS_VERSION] = row.harness_version
    if row.cost_usd is not None or row.estimated_cost_usd is not None:
        attrs[shared.INVOCATION_COST_USD] = _cost(total)
        attrs[shared.INVOCATION_COST_ESTIMATED] = row.estimated_cost_usd is not None
    return EventRecord(attr.EVENT_INVOCATION, at, attrs)


def _events(facts: StepFacts, step: NodeStep, usage: list[UsageFact], end: datetime) -> tuple[EventRecord, ...]:
    """Root events, each clamped to the step's end. Hub steps match polls by node and window, never by epoch."""
    events = [_invocation(u, min(u.recorded_at, end)) for u in usage]
    if step.kind is StepKind.HUB:
        node_id = step.position.node_id
        events += [
            EventRecord(attr.EVENT_HUB_POLL, min(p.polled_at, end))
            for p in facts.hub_polls
            if p.node_id == node_id and step.start <= p.polled_at <= end
        ]
    bounce = next((b for b in facts.bounces if b.epoch == step.epoch), None)
    if bounce is not None and step.kind is not StepKind.GATE:
        events.append(EventRecord(attr.EVENT_BOUNCE, min(bounce.recorded_at, end), {attr.BOUNCE_CAUSE: bounce.cause}))
    return tuple(sorted(events, key=lambda e: e.time))


def _claimable_at(facts: StepFacts, before: datetime) -> datetime:
    """The latest instant at or before ``before`` the chunk became claimable."""
    instants = [p.promoted_at for p in facts.promotions]
    instants += [r.released_at for r in facts.route_released]
    instants += [r.requeued_at for r in facts.requeues]
    instants += [p.set_at for p in facts.pauses if not p.paused]
    instants += [m.met_at for m in facts.prerequisites_met]
    eligible = [at for at in instants if at <= before]
    return max(eligible, default=before)


def _claim_children(facts: StepFacts, step: NodeStep, steps: tuple[NodeStep, ...]) -> list[_Child]:
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
    children = [
        _Child(
            SpanRole.QUEUE,
            "",
            "queue wait",
            min(_claimable_at(facts, claim_at), claim_at),
            claim_at,
            {},
            attr.WAIT_QUEUE_MS,
        )
    ]
    if step.kind is StepKind.RUNNER:
        children.append(_Child(SpanRole.CLAIM, "", "claim", claim_at, step.start, {}, attr.WAIT_CLAIM_MS))
    return children


def _ask_child(question: QuestionRecord, end: datetime) -> _Child:
    finish = question.answered_at if question.answered_at is not None else end
    extra: dict[str, AttributeValue] = {attr.ASK_ANSWERED: question.answered_at is not None}
    if finish < question.asked_at:
        finish = question.asked_at
        extra[attr.CLOCK_SKEW] = True
    return _Child(SpanRole.ASK, question.question_id, "ask", question.asked_at, finish, extra, attr.WAIT_ASK_MS)


def _pause_children(pauses: tuple[PauseRecord, ...], start: datetime, end: datetime) -> list[_Child]:
    ordered = sorted(pauses, key=lambda p: p.set_at)
    children: list[_Child] = []
    for pause in ordered:
        if not pause.paused or not start <= pause.set_at <= end:
            continue
        lift = next((p.set_at for p in ordered if not p.paused and p.set_at > pause.set_at), end)
        children.append(
            _Child(
                SpanRole.PAUSE,
                pause.id,
                "pause",
                pause.set_at,
                min(max(lift, pause.set_at), max(end, pause.set_at)),
                {},
                attr.WAIT_PAUSE_MS,
            )
        )
    return children


def _children(facts: StepFacts, step: NodeStep, steps: tuple[NodeStep, ...], end: datetime) -> list[_Child]:
    assert step.close is not None
    if step.kind is StepKind.GATE:
        if step.resolved_at is None:
            return []
        pickup_end = max(step.close.at, step.resolved_at)
        return [_Child(SpanRole.PICKUP, "", "decision pickup", step.resolved_at, pickup_end, {}, attr.WAIT_PICKUP_MS)]
    children = _claim_children(facts, step, steps)
    children += [_ask_child(q, end) for q in facts.questions if q.epoch == step.epoch]
    children += _pause_children(facts.pauses, step.start, end)
    if step.kind is StepKind.HUB:
        node_id = step.position.node_id
        for slot in facts.hub_exec_slots:
            if slot.node_id != node_id or not step.start <= slot.acquired_at <= end:
                continue
            released = min(slot.released_at, end) if slot.released_at is not None else end
            children.append(
                _Child(
                    SpanRole.HUB_EXEC, slot.slot_id, "hub exec", slot.acquired_at, max(released, slot.acquired_at), {}
                )
            )
    return children


def _link_reason(facts: StepFacts, step: NodeStep, previous: NodeStep) -> str:
    """The first reason that applies, in the spec's precedence."""
    if step.preceded_by is PrecededBy.RESTART:
        return "restart"
    if previous.position.graph_id != step.position.graph_id:
        return "migration"
    if previous.kind is not StepKind.GATE and any(b.epoch == previous.epoch for b in facts.bounces):
        return "bounce"
    if (
        previous.close is not None
        and previous.close.outcome not in _MOVEMENTS
        and previous.position.node_name == step.position.node_name
    ):
        return "retry"
    return "next"


def _link(facts: StepFacts, step: NodeStep, steps: tuple[NodeStep, ...]) -> tuple[LinkRecord, ...]:
    index = next((i for i, s in enumerate(steps) if s.key == step.key), None)
    if not index:
        return ()
    previous = steps[index - 1]
    return (LinkRecord(step_root(previous.key), {attr.LINK_REASON: _link_reason(facts, step, previous)}),)


def assemble_step(facts: StepFacts, step: NodeStep) -> tuple[SpanRecord, ...]:
    """The finished spans of one closed step, root first. An open step is refused: only closed steps are told."""
    if step.close is None:
        raise ValueError(f"step {step.key.text()} is open; only closed steps are assembled")
    steps = identify_steps(facts)
    end = _end_of(step)
    usage = _usage_of(facts, step)
    dims = _dimensions(facts, step, usage)
    children = _children(facts, step, steps, end)

    waits: dict[str, int] = dict.fromkeys(
        (attr.WAIT_QUEUE_MS, attr.WAIT_CLAIM_MS, attr.WAIT_ASK_MS, attr.WAIT_PAUSE_MS, attr.WAIT_PICKUP_MS), 0
    )
    for child in children:
        if child.wait is not None:
            waits[child.wait] += _ms(child.start, child.end)

    gate = step.kind is StepKind.GATE
    root_context = step_root(step.key)
    root = SpanRecord(
        context=root_context,
        parent_span_id=None,
        name=f"{'gate' if gate else 'step'} {step.position.node_name}",
        start=step.start,
        end=end,
        attributes={**dims, **_measures(usage, waits)},
        status=SpanStatus.ERROR if step.close.outcome is StepOutcome.ESCALATED else SpanStatus.UNSET,
        events=() if gate else _events(facts, step, usage, end),
        links=_link(facts, step, steps),
    )
    spans = [root]
    for child in children:
        spans.append(
            SpanRecord(
                context=DerivedContext.of(step.key, child.role, child.discriminator),
                parent_span_id=root_context.span_id,
                name=child.name,
                start=child.start,
                end=child.end,
                attributes={**dims, **child.extra},
            )
        )
    return tuple(spans)

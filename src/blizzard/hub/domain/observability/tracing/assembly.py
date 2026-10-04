"""Assembling one closed step into its finished span records, its root parented on the work root.

Contract: ``blizzard-product:/delivered/tracing/fleet-spans/spec/spans.md`` §Spans in a step's trace, §Span events,
§Links, §Status, §Attributes, §GenAI usage and §What never leaves. Pure: a :class:`StepFacts` and a closed
:class:`NodeStep` in, an ordered tuple of :class:`FinishedSpan` out — root first — with no clock, env read or I/O.
Every dimension and measure is read off the step's :class:`StepSummary`; what stays here is span-shaped."""

from __future__ import annotations

from datetime import datetime

from blizzard.foundation import trace_attributes as shared
from blizzard.foundation.trace_ids import DerivedContext, SpanRole, chunk_span_id, step_root
from blizzard.foundation.trace_spans import (
    AttributeValue,
    FinishedSpan,
    SpanEvent,
    SpanLink,
    SpanStatus,
)
from blizzard.hub.domain.chunk.model import UsageFact
from blizzard.hub.domain.observability.tracing import attributes as attr
from blizzard.hub.domain.observability.tracing.facts import StepFacts
from blizzard.hub.domain.observability.tracing.steps import NodeStep, PrecededBy, StepKind, StepOutcome
from blizzard.hub.domain.observability.tracing.summary import (
    Interval,
    IntervalKind,
    StepSummary,
    invocation_cost_usd,
    summarize_step,
)

_MOVEMENTS = frozenset({StepOutcome.TRANSITIONED, StepOutcome.MIGRATED, StepOutcome.DECIDED, StepOutcome.RESTARTED})
_HUMAN = "human"


_ROLES = {
    IntervalKind.QUEUE: (SpanRole.QUEUE, "queue wait", attr.WAIT_QUEUE_MS),
    IntervalKind.CLAIM: (SpanRole.CLAIM, "claim", attr.WAIT_CLAIM_MS),
    IntervalKind.ASK: (SpanRole.ASK, "ask", attr.WAIT_ASK_MS),
    IntervalKind.PAUSE: (SpanRole.PAUSE, "pause", attr.WAIT_PAUSE_MS),
    IntervalKind.PICKUP: (SpanRole.PICKUP, "decision pickup", attr.WAIT_PICKUP_MS),
    IntervalKind.HUB_EXEC: (SpanRole.HUB_EXEC, "hub exec", None),
}


def step_dimensions(summary: StepSummary) -> dict[str, AttributeValue]:
    dims: dict[str, AttributeValue] = {
        shared.CHUNK_ID: summary.chunk_id,
        shared.GRAPH_NAME: summary.graph_name,
        shared.GRAPH_ID: summary.graph_id,
        shared.NODE_NAME: summary.node_name,
        shared.NODE_ID: summary.node_id,
        shared.NODE_EXECUTOR: summary.node_executor,
        shared.STEP_EPOCH: summary.epoch,
        shared.STEP_VISIT: summary.visit,
        attr.STEP_OUTCOME: summary.outcome.value,
    }
    if summary.work_refs:
        dims[shared.CHUNK_WORK_REFS] = summary.work_refs
    optional: dict[str, str | None] = {
        attr.STEP_CHOICE: summary.choice,
        attr.STEP_TO_NODE_NAME: summary.to_node_name,
        attr.STEP_PRECEDED_BY: summary.preceded_by.value if summary.preceded_by is not None else None,
        attr.RUNNER_ID: summary.runner_id,
        shared.HARNESS_ID: summary.harness_id,
        attr.BOUNCE_CAUSE: summary.bounce_cause,
    }
    dims.update({k: v for k, v in optional.items() if v is not None})
    if summary.models:
        dims[attr.STEP_MODELS] = summary.models
    return dims


def step_usage(summary: StepSummary) -> dict[str, AttributeValue]:
    return {
        attr.STEP_INPUT_TOKENS: summary.input_tokens,
        attr.STEP_OUTPUT_TOKENS: summary.output_tokens,
        attr.STEP_CACHE_READ_TOKENS: summary.cache_read_tokens,
        attr.STEP_CACHE_CREATE_TOKENS: summary.cache_create_tokens,
        attr.STEP_COST_USD: summary.folded_cost_usd(),
        attr.STEP_COST_ESTIMATED: summary.cost_estimated_usd is not None,
        attr.STEP_COST_PARTIAL: summary.cost_partial,
    }


def step_waits(summary: StepSummary) -> dict[str, AttributeValue]:
    return {
        attr.WAIT_QUEUE_MS: summary.wait_queue_ms,
        attr.WAIT_CLAIM_MS: summary.wait_claim_ms,
        attr.WAIT_ASK_MS: summary.wait_ask_ms,
        attr.WAIT_PAUSE_MS: summary.wait_pause_ms,
        attr.WAIT_PICKUP_MS: summary.wait_pickup_ms,
    }


def _measures(summary: StepSummary) -> dict[str, AttributeValue]:
    return {**step_usage(summary), **step_waits(summary)}


def _invocation(row: UsageFact, at: datetime) -> SpanEvent:
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
        attrs[shared.INVOCATION_COST_USD] = invocation_cost_usd(row)
        attrs[shared.INVOCATION_COST_ESTIMATED] = row.estimated_cost_usd is not None
    return SpanEvent(attr.EVENT_INVOCATION, at, attrs)


def _events(facts: StepFacts, step: NodeStep, summary: StepSummary) -> tuple[SpanEvent, ...]:
    """Root events, each clamped to the step's end. Hub steps match polls by node and window, never by epoch."""
    end = summary.ended_at
    events = [_invocation(u, min(u.recorded_at, end)) for u in summary.usage]
    if step.kind is StepKind.HUB:
        node_id = step.position.node_id
        events += [
            SpanEvent(attr.EVENT_HUB_POLL, min(p.polled_at, end))
            for p in facts.hub_polls
            if p.node_id == node_id and step.start <= p.polled_at <= end
        ]
    bounce = next((b for b in facts.bounces if b.epoch == step.epoch), None)
    if bounce is not None and step.kind is not StepKind.GATE:
        events.append(SpanEvent(attr.EVENT_BOUNCE, min(bounce.recorded_at, end), {attr.BOUNCE_CAUSE: bounce.cause}))
    return tuple(sorted(events, key=lambda e: e.time))


def _child(step: NodeStep, parent: FinishedSpan, dims: dict[str, AttributeValue], interval: Interval) -> FinishedSpan:
    role, name, _ = _ROLES[interval.kind]
    extra: dict[str, AttributeValue] = {}
    if interval.kind is IntervalKind.ASK:
        extra[attr.ASK_ANSWERED] = bool(interval.answered)
        if interval.clock_skew:
            extra[attr.CLOCK_SKEW] = True
    return FinishedSpan(
        context=DerivedContext.of(step.key, role, interval.discriminator),
        parent_span_id=parent.context.span_id,
        name=name,
        start=interval.start,
        end=interval.end,
        attributes={**dims, **extra},
    )


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


def _link(facts: StepFacts, step: NodeStep, steps: tuple[NodeStep, ...]) -> tuple[SpanLink, ...]:
    index = next((i for i, s in enumerate(steps) if s.key == step.key), None)
    if not index:
        return ()
    previous = steps[index - 1]
    return (SpanLink(step_root(previous.key), {attr.LINK_REASON: _link_reason(facts, step, previous)}),)


def assemble_step(facts: StepFacts, step: NodeStep, steps: tuple[NodeStep, ...]) -> tuple[FinishedSpan, ...]:
    """The finished spans of one closed step of ``steps`` — the chunk's identified steps — root first.
    An open step is refused: only closed steps are told."""
    if step.close is None:
        raise ValueError(f"step {step.key.text()} is open; only closed steps are assembled")
    summary = summarize_step(facts, step, steps)
    dims = step_dimensions(summary)
    gate = step.kind is StepKind.GATE
    root = FinishedSpan(
        context=step_root(step.key),
        parent_span_id=chunk_span_id(facts.chunk_id),
        name=f"{'gate' if gate else 'step'} {summary.node_name}",
        start=summary.started_at,
        end=summary.ended_at,
        attributes={**dims, **_measures(summary)},
        status=SpanStatus.ERROR if summary.outcome is StepOutcome.ESCALATED else SpanStatus.UNSET,
        events=() if gate else _events(facts, step, summary),
        links=_link(facts, step, steps),
    )
    return (root, *(_child(step, root, dims, interval) for interval in summary.intervals))

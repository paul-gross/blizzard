"""Assembling one closed lease into its finished span records.

Contract: ``blizzard-product:/delivered/tracing/runner-spans/spec/spans.md``, ids and GenAI usage per fleet-spans
§Identity and §GenAI usage. Pure: a :class:`LeaseTraceFacts` in, an ordered tuple of :class:`FinishedSpan` out —
``worker`` first, then its children by start — with no clock, env read or I/O."""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum

from blizzard.foundation import trace_attributes as shared
from blizzard.foundation.leases import LeaseClosureReason
from blizzard.foundation.roles import domain_model
from blizzard.foundation.trace_ids import DerivedContext, RunnerSpanRole, SpanRole, StepKey, span_id
from blizzard.foundation.trace_spans import AttributeValue, FinishedSpan, SpanEvent
from blizzard.runner.leases import closure
from blizzard.runner.tracing import attributes as attr
from blizzard.runner.tracing.facts import BoundaryFact, LeaseTraceFacts, SpawnFact, TokenUsageFact

_EXECUTOR = "runner"


class EndSource(StrEnum):
    """Which fact ended an invocation, in the spec's precedence."""

    SESSION_END = "session_end"
    NEXT_INVOCATION = "next_invocation"
    LEASE_CLOSE = "lease_close"


@domain_model
@dataclass(frozen=True)
class _Window:
    """A child's interval, already clamped to the lease's close."""

    start: datetime
    end: datetime

    @classmethod
    def within(cls, start: datetime, end: datetime, close: datetime) -> _Window:
        start = min(start, close)
        return cls(start, max(start, min(end, close)))

    def holds(self, at: datetime) -> bool:
        return self.start <= at <= self.end


def close_reason(reason: str) -> str:
    """The published close reason: either mint reason reads ``escalated``."""
    return LeaseClosureReason.ESCALATED if reason in closure.MINT_REASONS else reason


def _dimensions(facts: LeaseTraceFacts) -> dict[str, AttributeValue]:
    lease, context = facts.lease, facts.context
    dims: dict[str, AttributeValue] = {
        shared.CHUNK_ID: lease.chunk_id,
        shared.GRAPH_ID: context.graph_id,
        shared.NODE_ID: context.node_id,
        shared.NODE_NAME: context.node_name,
        shared.NODE_EXECUTOR: _EXECUTOR,
        shared.STEP_EPOCH: lease.epoch,
        attr.RUNNER_ID: facts.runner.runner_id,
        attr.RUNNER_NAME: facts.runner.runner_name,
        attr.LEASE_ID: lease.lease_id,
    }
    if context.graph_name is not None:
        dims[shared.GRAPH_NAME] = context.graph_name
    if context.work_refs:
        dims[shared.CHUNK_WORK_REFS] = context.work_refs
    return dims


def _set_present(attrs: dict[str, AttributeValue], values: dict[str, AttributeValue | None]) -> None:
    attrs.update({k: v for k, v in values.items() if v is not None})


def _earliest(candidates: Iterable[tuple[datetime | None, EndSource]]) -> tuple[datetime, EndSource] | None:
    """The earliest present candidate; a tie goes to the earlier-listed source."""
    present = [(at, source) for at, source in candidates if at is not None]
    return min(present, key=lambda c: c[0]) if present else None


def _invocation_end(
    facts: LeaseTraceFacts, boundary: BoundaryFact, following: BoundaryFact | None, close: datetime
) -> tuple[datetime, EndSource]:
    ended = [e.ended_at for e in facts.session_ends if e.ended_at > boundary.opened_at]
    found = _earliest(
        (
            (min(ended, default=None), EndSource.SESSION_END),
            (following.opened_at if following is not None else None, EndSource.NEXT_INVOCATION),
            (boundary.closed_at, EndSource.LEASE_CLOSE),
        )
    )
    return found if found is not None else (close, EndSource.LEASE_CLOSE)


def _usage_for(facts: LeaseTraceFacts, boundary: BoundaryFact) -> list[TokenUsageFact]:
    """Usage matched by generation and side: ``judge`` rows to the judge boundary, any other kind to the worker's."""
    judge = boundary.kind == "judge"
    rows = (u for u in facts.usage if u.generation == boundary.generation and (u.kind == "judge") == judge)
    return sorted(rows, key=lambda u: (u.recorded_at, u.id))


def _measures(usage: Sequence[TokenUsageFact]) -> dict[str, AttributeValue]:
    if not usage:
        return {}
    measures: dict[str, AttributeValue] = {
        shared.GEN_AI_RESPONSE_MODEL: usage[-1].model,
        **shared.genai_usage(
            input_tokens=sum(u.input_tokens for u in usage),
            output_tokens=sum(u.output_tokens for u in usage),
            cache_read_tokens=sum(u.cache_read_tokens for u in usage),
            cache_create_tokens=sum(u.cache_create_tokens for u in usage),
        ),
    }
    billed = [u.cost_usd for u in usage if u.cost_usd is not None]
    estimated = [u.estimated_cost_usd for u in usage if u.estimated_cost_usd is not None]
    if billed or estimated:
        measures[shared.INVOCATION_COST_USD] = sum(billed) + sum(estimated)
        measures[shared.INVOCATION_COST_ESTIMATED] = bool(estimated)
    return measures


def _spawn_of(spawns: Sequence[SpawnFact], generation: int) -> SpawnFact | None:
    """The n-th spawn row on the lease, by id, is worker generation n."""
    return spawns[generation - 1] if generation <= len(spawns) else None


def _harness(spawn: SpawnFact | None, usage: Sequence[TokenUsageFact], judge: bool) -> tuple[str | None, str | None]:
    if not judge:
        return (spawn.harness_id, spawn.harness_version) if spawn is not None else (None, None)
    known = [u for u in usage if u.harness_id is not None]
    return (known[-1].harness_id, known[-1].harness_version) if known else (None, None)


def _invocation(
    facts: LeaseTraceFacts,
    key: StepKey,
    parent: int,
    boundaries: Sequence[BoundaryFact],
    index: int,
    spawns: Sequence[SpawnFact],
    dims: dict[str, AttributeValue],
) -> FinishedSpan:
    boundary = boundaries[index]
    following = boundaries[index + 1] if index + 1 < len(boundaries) else None
    close = _lease_close(facts)
    end, source = _invocation_end(facts, boundary, following, close)
    window = _Window.within(boundary.opened_at, end, close)
    judge = boundary.kind == "judge"
    spawn = None if judge else _spawn_of(spawns, boundary.generation)
    usage = _usage_for(facts, boundary)
    harness_id, harness_version = _harness(spawn, usage, judge)
    session_name = facts.context.session_name
    attrs: dict[str, AttributeValue] = {
        **dims,
        shared.INVOCATION_KIND: "resume" if boundary.kind == "nudge" else boundary.kind,
        attr.INVOCATION_NUDGE: boundary.kind == "nudge",
        attr.INVOCATION_GENERATION: boundary.generation,
        attr.INVOCATION_END_SOURCE: source.value,
        attr.GEN_AI_OPERATION_NAME: attr.INVOKE_AGENT,
        **_measures(usage),
    }
    _set_present(
        attrs,
        {
            attr.SESSION_NAME: session_name,
            attr.GEN_AI_AGENT_NAME: session_name,
            attr.GEN_AI_REQUEST_MODEL: facts.context.resolved_model,
            attr.GEN_AI_CONVERSATION_ID: spawn.session_id if spawn is not None else None,
            shared.HARNESS_ID: harness_id,
            shared.HARNESS_VERSION: harness_version,
        },
    )
    events: list[SpanEvent] = []
    if spawn is not None and spawn.identified_at is not None and window.holds(spawn.identified_at):
        events.append(SpanEvent(attr.EVENT_SESSION_IDENTIFIED, spawn.identified_at))
    events += [
        SpanEvent(attr.EVENT_SESSION_END, e.ended_at)
        for e in facts.session_ends
        if e.ended_at > boundary.opened_at and window.holds(e.ended_at)
    ]
    for sample in facts.context_samples:
        if window.holds(sample.sampled_at):
            tokens = {} if sample.context_tokens is None else {attr.CONTEXT_TOKENS: sample.context_tokens}
            events.append(SpanEvent(attr.EVENT_CONTEXT_SAMPLE, sample.sampled_at, tokens))
    return FinishedSpan(
        context=DerivedContext.of(key, RunnerSpanRole.INVOCATION, f"{boundary.generation}/{boundary.kind}"),
        parent_span_id=parent,
        name=f"{attr.INVOKE_AGENT} {session_name}" if session_name is not None else attr.INVOKE_AGENT,
        start=window.start,
        end=window.end,
        attributes=attrs,
        events=tuple(sorted(events, key=lambda e: e.time)),
    )


def _child(
    key: StepKey,
    parent: int,
    role: RunnerSpanRole,
    discriminator: str,
    name: str,
    window: _Window,
    attrs: dict[str, AttributeValue],
) -> FinishedSpan:
    return FinishedSpan(
        context=DerivedContext.of(key, role, discriminator),
        parent_span_id=parent,
        name=name,
        start=window.start,
        end=window.end,
        attributes=attrs,
    )


def _waits(
    facts: LeaseTraceFacts,
    key: StepKey,
    parent: int,
    boundaries: Sequence[BoundaryFact],
    dims: dict[str, AttributeValue],
) -> list[FinishedSpan]:
    close = _lease_close(facts)
    spans: list[FinishedSpan] = []
    for park in facts.parks:
        resumed = [
            r.resumed_at
            for r in facts.park_resumes
            if r.question_id == park.question_id and r.resumed_at >= park.parked_at
        ]
        window = _Window.within(park.parked_at, min(resumed, default=close), close)
        spans.append(_child(key, parent, RunnerSpanRole.ASK_PARK, park.question_id, "parked on ask", window, dims))
    for pause in facts.pause_parks:
        resumed = [r.resumed_at for r in facts.pause_resumes if r.resumed_at >= pause.parked_at]
        window = _Window.within(pause.parked_at, min(resumed, default=close), close)
        spans.append(_child(key, parent, RunnerSpanRole.PAUSE_PARK, str(pause.id), "parked on pause", window, dims))
    for overload in facts.overloads:
        ends = [b.opened_at for b in boundaries if b.opened_at > overload.observed_at]
        ends += [overload.resume_after] if overload.resume_after is not None else []
        window = _Window.within(overload.observed_at, min(ends, default=close), close)
        extra = {**dims, attr.OVERLOAD_STREAK: overload.streak_ordinal}
        spans.append(
            _child(key, parent, RunnerSpanRole.OVERLOAD, str(overload.id), "provider overload backoff", window, extra)
        )
    for takeover in facts.takeovers:
        ended = [e.ended_at for e in facts.takeover_ends if e.takeover_id == takeover.takeover_id]
        window = _Window.within(takeover.opened_at, min(ended, default=close), close)
        spans.append(_child(key, parent, RunnerSpanRole.TAKEOVER, takeover.takeover_id, "takeover", window, dims))
    return spans


def _worker_events(facts: LeaseTraceFacts) -> tuple[SpanEvent, ...]:
    """Nudges, then each checks run's per-check events and its summary, all clamped to the worker's span."""
    events = [SpanEvent(attr.EVENT_NUDGE, n.nudged_at) for n in facts.nudges]
    for ran in sorted(facts.checks_ran, key=lambda r: r.id):
        results = sorted((c for c in facts.check_results if c.epoch == ran.epoch), key=lambda c: c.id)
        events += [
            SpanEvent(attr.EVENT_CHECK, ran.ran_at, {attr.CHECK_INDEX: index, attr.CHECK_PASSED: result.passed})
            for index, result in enumerate(results, start=1)
        ]
        summary = {attr.CHECKS_PASSED: all(c.passed for c in results), attr.CHECKS_COUNT: len(results)}
        events.append(SpanEvent(attr.EVENT_CHECKS_RAN, ran.ran_at, summary))
    window = _Window.within(facts.lease.created_at, _lease_close(facts), _lease_close(facts))
    clamped = [SpanEvent(e.name, min(max(e.time, window.start), window.end), e.attributes) for e in events]
    return tuple(sorted(clamped, key=lambda e: e.time))


def _lease_close(facts: LeaseTraceFacts) -> datetime:
    """The worker span's end: the closure, never before the lease was minted."""
    return max(facts.closure.closed_at, facts.lease.created_at)


def assemble_lease(facts: LeaseTraceFacts) -> tuple[FinishedSpan, ...]:
    """The finished spans of one closed lease, ``worker`` first; ``()`` for a lease whose spawn was never identified."""
    if not any(s.identified_at is not None for s in facts.spawns):
        return ()
    lease = facts.lease
    key = StepKey.attempt(lease.chunk_id, lease.epoch)
    dims = _dimensions(facts)
    spawns = sorted(facts.spawns, key=lambda s: s.id)
    latest = spawns[-1]
    worker_attrs: dict[str, AttributeValue] = {**dims, attr.LEASE_CLOSE_REASON: close_reason(facts.closure.reason)}
    _set_present(
        worker_attrs,
        {
            attr.SESSION_NAME: facts.context.session_name,
            shared.HARNESS_ID: latest.harness_id,
            shared.HARNESS_VERSION: latest.harness_version,
            attr.MODEL_RESOLVED: facts.context.resolved_model,
            attr.EFFORT_RESOLVED: facts.context.resolved_effort,
        },
    )
    worker = FinishedSpan(
        context=DerivedContext.of(key, RunnerSpanRole.WORKER, lease.lease_id),
        parent_span_id=span_id(key, SpanRole.STEP),
        name=f"worker {facts.context.node_name}",
        start=lease.created_at,
        end=_lease_close(facts),
        attributes=worker_attrs,
        events=_worker_events(facts),
    )
    parent = worker.context.span_id
    boundaries = sorted(facts.boundaries, key=lambda b: (b.opened_at, b.id))
    children = [_invocation(facts, key, parent, boundaries, i, spawns, dims) for i in range(len(boundaries))]
    children += _waits(facts, key, parent, boundaries, dims)
    return (worker, *sorted(children, key=lambda s: s.start))

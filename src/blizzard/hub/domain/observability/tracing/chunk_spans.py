"""A chunk told as a whole, in two traces: its work root, and its lifetime trace with the waits no step owns.

Contract: ``docs/deployment/tracing.md`` §Trace and span ids. Every interval derives from the
fact log, so a replay tells the sweep's ids and instants. Pure: a :class:`StepFacts` in, :class:`FinishedSpan` out."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum

from blizzard.foundation import trace_attributes as shared
from blizzard.foundation.migration_source import MigrationSource
from blizzard.foundation.roles import domain_model
from blizzard.foundation.trace_ids import (
    ChunkRole,
    chunk_context,
    instant_text,
    lifetime_context,
    step_root,
)
from blizzard.foundation.trace_spans import AttributeValue, FinishedSpan, SpanLink, SpanStatus
from blizzard.hub.domain.chunk.model import UsageTotal
from blizzard.hub.domain.graph.model import RESERVED_TERMINAL
from blizzard.hub.domain.observability.tracing import attributes as attr
from blizzard.hub.domain.observability.tracing.assembly import step_dimensions, step_usage, step_waits
from blizzard.hub.domain.observability.tracing.facts import StepFacts
from blizzard.hub.domain.observability.tracing.steps import LinkReason, StepKind, StepOutcome, identify_steps
from blizzard.hub.domain.observability.tracing.summary import IntervalKind, StepSummary, folded_cost, summarize_step


class ChunkOutcome(StrEnum):
    """``blizzard.chunk.outcome``."""

    DONE = "done"
    STOPPED = "stopped"


@domain_model
@dataclass(frozen=True)
class ChunkEnd:
    outcome: ChunkOutcome
    at: datetime


@domain_model
@dataclass(frozen=True)
class _Wait:
    role: ChunkRole
    name: str
    start: datetime
    end: datetime


def chunk_end(facts: StepFacts) -> ChunkEnd | None:
    """The chunk's first terminal fact, or ``None``. On an exact tie a completion beats a stop, as in
    `src/blizzard/hub/domain/chunk/model.py`'s `ChunkFacts`."""
    found: list[tuple[datetime, int, ChunkOutcome]] = []
    found += [(t.recorded_at, 0, ChunkOutcome.DONE) for t in facts.transitions if t.to_node_id == RESERVED_TERMINAL]
    found += [(c.recorded_at, 1, ChunkOutcome.DONE) for c in facts.chunk_completed]
    found += [(s.recorded_at, 2, ChunkOutcome.STOPPED) for s in facts.chunk_stopped]
    if not found:
        return None
    at, _, outcome = min(found, key=lambda f: (f[0], f[1]))
    return ChunkEnd(outcome, at)


def completion_instant(facts: StepFacts) -> datetime | None:
    """When a chunk that stopped was hand-completed, or ``None``."""
    end = chunk_end(facts)
    if end is None or end.outcome is not ChunkOutcome.STOPPED:
        return None
    later = [c.recorded_at for c in facts.chunk_completed if c.recorded_at > end.at]
    return min(later, default=None)


def _ms(start: datetime, end: datetime) -> int:
    return max(0, round((end - start).total_seconds() * 1000))


def _backlog_end(facts: StepFacts, finish: datetime) -> datetime:
    """When the chunk left the backlog: its first promotion, else its first lease, else its finish."""
    ends = [finish, *(p.promoted_at for p in facts.promotions), *(lease.minted_at for lease in facts.lease_facts)]
    return min(ends)


def _escalation_waits(facts: StepFacts, finish: datetime) -> list[_Wait]:
    restarts = [r.recorded_at for r in facts.restarts]
    restarts += [m.recorded_at for m in facts.migrations if m.source is MigrationSource.RESTART]
    waits: list[_Wait] = []
    for escalation in sorted(facts.escalations, key=lambda e: e.recorded_at):
        releases = [
            *(r.requeued_at for r in facts.requeues),
            *restarts,
            *(lease.minted_at for lease in facts.lease_facts),
        ]
        end = min([finish, *(at for at in releases if at > escalation.recorded_at)])
        waits.append(_Wait(ChunkRole.ESCALATION, "escalation wait", escalation.recorded_at, end))
    return waits


def _uncovered(
    start: datetime, end: datetime, held: list[tuple[datetime, datetime]]
) -> list[tuple[datetime, datetime]]:
    """The parts of ``[start, end)`` that no held interval covers, in order."""
    pieces: list[tuple[datetime, datetime]] = []
    cursor = start
    for held_start, held_end in sorted(held):
        if held_end <= cursor or held_start >= end:
            continue
        if held_start > cursor:
            pieces.append((cursor, held_start))
        cursor = max(cursor, held_end)
    if cursor < end:
        pieces.append((cursor, end))
    return pieces


def _pause_waits(facts: StepFacts, finish: datetime, held: list[tuple[datetime, datetime]]) -> list[_Wait]:
    """The stretches of a pause that no step span and no other chunk-level wait covers, including what is left of
    a pause that began inside a step once that step's span closed."""
    pauses: list[tuple[datetime, datetime]] = []
    began: datetime | None = None
    for pause in sorted(facts.pauses, key=lambda p: p.set_at):
        if pause.paused and began is None:
            began = pause.set_at
        elif not pause.paused and began is not None:
            pauses.append((began, pause.set_at))
            began = None
    if began is not None:
        pauses.append((began, finish))
    return [
        _Wait(ChunkRole.PAUSE, "pause wait", start, end)
        for pause_start, pause_end in pauses
        for start, end in _uncovered(pause_start, pause_end, held)
    ]


def _chunk_waits(facts: StepFacts, minted: datetime, finish: datetime) -> list[_Wait]:
    """The backlog and escalation waits, each ending no later than the finish."""
    waits = [_Wait(ChunkRole.BACKLOG, "backlog wait", minted, _backlog_end(facts, finish))]
    waits += _escalation_waits(facts, finish)
    return [_Wait(w.role, w.name, w.start, min(w.end, finish)) for w in waits]


def _step_extents(summaries: list[StepSummary], chunk_waits: list[_Wait]) -> list[tuple[datetime, datetime]]:
    """What each step's lifetime span covers, in step order. It starts at the earliest of its queue and claim waits,
    else its own start, but never before the end of a chunk wait that ends by its own start, and never after its own
    start. It ends at the later of its end and its close, which takes in a gate's decision pickup. Step spans never
    overlap: a span that would run past the next one's start ends there."""
    extents: list[tuple[datetime, datetime]] = []
    for summary in summaries:
        begins = [i.start for i in summary.intervals if i.kind in (IntervalKind.QUEUE, IntervalKind.CLAIM)]
        floors = [w.end for w in chunk_waits if w.end <= summary.started_at]
        if extents:
            floors.append(extents[-1][1])
        start = min(max([min([summary.started_at, *begins]), *floors]), summary.started_at)
        if extents and extents[-1][1] > start:
            extents[-1] = (extents[-1][0], max(extents[-1][0], start))
        extents.append((start, max(summary.ended_at, summary.closed_at, start)))
    return extents


def _waits(
    facts: StepFacts, finish: datetime, chunk_waits: list[_Wait], extents: list[tuple[datetime, datetime]]
) -> list[_Wait]:
    """The chunk's own waits, each clipped to what no step span shows, so a wait and a step never overlap."""
    clipped = [
        _Wait(w.role, w.name, start, end) for w in chunk_waits for start, end in _uncovered(w.start, w.end, extents)
    ]
    held = [*extents, *((w.start, w.end) for w in clipped)]
    waits = [*clipped, *_pause_waits(facts, finish, held)]
    return [_Wait(w.role, w.name, w.start, min(w.end, finish)) for w in waits if min(w.end, finish) > w.start]


def _dimensions(facts: StepFacts) -> dict[str, AttributeValue]:
    dims: dict[str, AttributeValue] = {shared.CHUNK_ID: facts.chunk_id}
    if facts.work_refs:
        dims[shared.CHUNK_WORK_REFS] = tuple(facts.work_refs)
    return dims


def _minted(facts: StepFacts) -> datetime:
    if facts.minted_at is None:
        raise ValueError(f"chunk {facts.chunk_id} carries no ingest instant; it cannot be told as a whole")
    return facts.minted_at


def _totals(facts: StepFacts) -> dict[str, AttributeValue]:
    total = UsageTotal.of(list(facts.usage))
    return {
        attr.CHUNK_STEPS: len(identify_steps(facts)),
        attr.CHUNK_BOUNCES: len(facts.bounces),
        attr.CHUNK_INPUT_TOKENS: total.input_tokens,
        attr.CHUNK_OUTPUT_TOKENS: total.output_tokens,
        attr.CHUNK_CACHE_READ_TOKENS: total.cache_read_tokens,
        attr.CHUNK_CACHE_CREATE_TOKENS: total.cache_create_tokens,
        attr.CHUNK_COST_USD: folded_cost(total.billed_cost_usd, total.estimated_cost_usd),
        attr.CHUNK_COST_ESTIMATED: total.estimated_cost_usd is not None,
        attr.CHUNK_COST_PARTIAL: total.cost_partial,
    }


def _summaries(facts: StepFacts) -> list[StepSummary]:
    steps = identify_steps(facts)
    return [summarize_step(facts, step, steps) for step in steps if step.close is not None]


def _finish(facts: StepFacts) -> tuple[ChunkEnd, datetime]:
    end = chunk_end(facts)
    if end is None:
        raise ValueError(f"chunk {facts.chunk_id} is unfinished; only a finished chunk is told as a whole")
    return end, max(end.at, _minted(facts))


def assemble_work(facts: StepFacts) -> tuple[FinishedSpan, ...]:
    """The ``chunk work`` root, from the first step's start to the chunk's finish; empty when no step closed."""
    end, finish = _finish(facts)
    summaries = _summaries(facts)
    if not summaries:
        return ()
    start = min(summary.started_at for summary in summaries)
    return (
        FinishedSpan(
            context=chunk_context(facts.chunk_id),
            parent_span_id=None,
            name="chunk work",
            start=start,
            end=max(finish, start),
            attributes={**_dimensions(facts), attr.CHUNK_OUTCOME: end.outcome.value, **_totals(facts)},
            links=(SpanLink(lifetime_context(facts.chunk_id), {attr.LINK_REASON: LinkReason.LIFETIME.value}),),
        ),
    )


def _lifetime_step(
    facts: StepFacts, summary: StepSummary, root: FinishedSpan, extent: tuple[datetime, datetime]
) -> FinishedSpan:
    return FinishedSpan(
        context=lifetime_context(facts.chunk_id, ChunkRole.STEP, summary.step_key.text()),
        parent_span_id=root.context.span_id,
        name=summary.node_name,
        start=extent[0],
        end=extent[1],
        attributes={
            **step_dimensions(summary),
            **step_usage(summary),
            **step_waits(summary),
            attr.STEP_KIND: "gate" if summary.kind is StepKind.GATE else "step",
        },
        status=SpanStatus.ERROR if summary.outcome is StepOutcome.ESCALATED else SpanStatus.UNSET,
        links=(SpanLink(step_root(summary.step_key), {attr.LINK_REASON: LinkReason.WORK.value}),),
        service_name=attr.CHUNK_SERVICE_NAME,
    )


def assemble_lifetime(facts: StepFacts) -> tuple[FinishedSpan, ...]:
    """The lifetime root, a span per closed step, then the wait spans. An unfinished chunk is refused."""
    end, finish = _finish(facts)
    minted = _minted(facts)
    backlog_end = _backlog_end(facts, finish)
    dims = _dimensions(facts)
    root = FinishedSpan(
        context=lifetime_context(facts.chunk_id),
        parent_span_id=None,
        name="chunk",
        start=minted,
        end=finish,
        attributes={
            **dims,
            attr.CHUNK_OUTCOME: end.outcome.value,
            attr.CHUNK_BACKLOG_MS: _ms(minted, backlog_end),
            attr.CHUNK_ACTIVE_MS: _ms(backlog_end, finish),
            **_totals(facts),
        },
        service_name=attr.CHUNK_SERVICE_NAME,
    )
    summaries = _summaries(facts)
    chunk_waits = _chunk_waits(facts, minted, finish)
    extents = _step_extents(summaries, chunk_waits)
    steps = [_lifetime_step(facts, summary, root, extent) for summary, extent in zip(summaries, extents, strict=True)]
    waits = [
        FinishedSpan(
            context=lifetime_context(facts.chunk_id, wait.role, instant_text(wait.start)),
            parent_span_id=root.context.span_id,
            name=wait.name,
            start=wait.start,
            end=wait.end,
            attributes=dims,
            service_name=attr.CHUNK_SERVICE_NAME,
        )
        for wait in _waits(facts, finish, chunk_waits, extents)
    ]
    return (root, *steps, *waits)


def assemble_completion(facts: StepFacts) -> tuple[FinishedSpan, ...]:
    """The zero-length ``chunk completed`` marker of a stopped chunk later hand-completed."""
    at = completion_instant(facts)
    if at is None:
        raise ValueError(f"chunk {facts.chunk_id} was not hand-completed after a stop")
    return (
        FinishedSpan(
            context=lifetime_context(facts.chunk_id, ChunkRole.COMPLETED, instant_text(at)),
            parent_span_id=lifetime_context(facts.chunk_id).span_id,
            name="chunk completed",
            start=at,
            end=at,
            attributes={**_dimensions(facts), attr.CHUNK_OUTCOME: ChunkOutcome.DONE.value},
            service_name=attr.CHUNK_SERVICE_NAME,
        ),
    )

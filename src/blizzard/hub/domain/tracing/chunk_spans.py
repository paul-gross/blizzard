"""A chunk told as a whole: its span, the waits no step owns, and a completion marker.

Contract: ``docs/deployment/tracing.md`` §Trace and span ids. Every interval derives from the
fact log, so a replay tells the sweep's ids and instants. Pure: a :class:`StepFacts` in, :class:`SpanRecord` out."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum

from blizzard.foundation import trace_attributes as shared
from blizzard.foundation.trace_ids import ChunkRole, chunk_context, chunk_span_id
from blizzard.foundation.trace_spans import AttributeValue, SpanRecord
from blizzard.hub.domain.graph import RESERVED_TERMINAL
from blizzard.hub.domain.tracing import attributes as attr
from blizzard.hub.domain.tracing.facts import StepFacts
from blizzard.hub.domain.tracing.steps import identify_steps
from blizzard.hub.domain.tracing.summary import folded_cost
from blizzard.hub.domain.work import MigrationSource, UsageTotal


class ChunkOutcome(StrEnum):
    """``blizzard.chunk.outcome``."""

    DONE = "done"
    STOPPED = "stopped"


@dataclass(frozen=True)
class ChunkEnd:
    outcome: ChunkOutcome
    at: datetime


@dataclass(frozen=True)
class _Wait:
    role: ChunkRole
    name: str
    start: datetime
    end: datetime


def chunk_end(facts: StepFacts) -> ChunkEnd | None:
    """The chunk's first terminal fact, or ``None``. On an exact tie a completion beats a stop, as in status."""
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


def _pause_waits(facts: StepFacts, finish: datetime, other_waits: list[_Wait]) -> list[_Wait]:
    """The stretches of a pause that no step and no other chunk-level wait covers, including what is left of a
    pause that began inside a step once that step closed."""
    held = [(step.start, step.close.at) for step in identify_steps(facts) if step.close is not None]
    held += [(wait.start, wait.end) for wait in other_waits]
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


def _waits(facts: StepFacts, minted: datetime, finish: datetime) -> list[_Wait]:
    waits = [_Wait(ChunkRole.BACKLOG, "backlog wait", minted, _backlog_end(facts, finish))]
    waits += _escalation_waits(facts, finish)
    waits += _pause_waits(facts, finish, waits)
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


def assemble_chunk(facts: StepFacts) -> tuple[SpanRecord, ...]:
    """The chunk span, then its wait spans. An unfinished chunk is refused."""
    end = chunk_end(facts)
    if end is None:
        raise ValueError(f"chunk {facts.chunk_id} is unfinished; only a finished chunk is told as a whole")
    minted = _minted(facts)
    finish = max(end.at, minted)
    backlog_end = _backlog_end(facts, finish)
    dims = _dimensions(facts)
    chunk = chunk_context(facts.chunk_id)
    root = SpanRecord(
        context=chunk,
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
    )
    waits = [
        SpanRecord(
            context=chunk_context(facts.chunk_id, wait.role, wait.start),
            parent_span_id=chunk.span_id,
            name=wait.name,
            start=wait.start,
            end=wait.end,
            attributes=dims,
        )
        for wait in _waits(facts, minted, finish)
    ]
    return (root, *waits)


def assemble_completion(facts: StepFacts) -> tuple[SpanRecord, ...]:
    """The zero-length ``chunk completed`` marker of a stopped chunk later hand-completed."""
    at = completion_instant(facts)
    if at is None:
        raise ValueError(f"chunk {facts.chunk_id} was not hand-completed after a stop")
    return (
        SpanRecord(
            context=chunk_context(facts.chunk_id, ChunkRole.COMPLETED, at),
            parent_span_id=chunk_span_id(facts.chunk_id),
            name="chunk completed",
            start=at,
            end=at,
            attributes={**_dimensions(facts), attr.CHUNK_OUTCOME: ChunkOutcome.DONE.value},
        ),
    )

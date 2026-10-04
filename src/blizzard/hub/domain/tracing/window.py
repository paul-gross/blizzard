"""The closed steps and finished chunks of a window — what a sweep tells after its cursor, or a replay between instants.

Contract: ``blizzard-product:/delivered/tracing/fleet-spans/spec/emission.md`` §What a sweep does and §The cursor.
A close or finish is known only from a chunk's whole facts, so the window reads candidates by closing-fact time,
hydrates them whole, and selects in the domain."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime

from blizzard.foundation.trace_spans import SpanRecord
from blizzard.hub.domain.tracing.assembly import assemble_step
from blizzard.hub.domain.tracing.chunk_spans import (
    assemble_completion,
    assemble_lifetime,
    assemble_work,
    chunk_end,
    completion_instant,
)
from blizzard.hub.domain.tracing.cursor import CursorKey
from blizzard.hub.domain.tracing.facts import StepFacts
from blizzard.hub.domain.tracing.repository import IReadTraceSteps
from blizzard.hub.domain.tracing.steps import NodeStep, identify_steps


@dataclass(frozen=True)
class ClosedStep:
    key: CursorKey
    step: NodeStep
    facts: StepFacts
    steps: tuple[NodeStep, ...]


@dataclass(frozen=True)
class FinishedChunk:
    """A chunk told as a whole at its finish, or with ``completion`` at a later hand-completion."""

    key: CursorKey
    facts: StepFacts
    completion: bool = False


TraceItem = ClosedStep | FinishedChunk


@dataclass(frozen=True)
class TraceWindow:
    """Up to a limit of items in cursor order, and ``position`` — where a pass that told every
    one of them may move the cursor. ``position`` equals the window's ``since`` when nothing was proven."""

    items: tuple[TraceItem, ...]
    position: CursorKey

    def closed_steps(self) -> tuple[ClosedStep, ...]:
        return tuple(i for i in self.items if isinstance(i, ClosedStep))

    def finished_chunks(self) -> tuple[FinishedChunk, ...]:
        return tuple(i for i in self.items if isinstance(i, FinishedChunk))


def _chunk_items(chunk: StepFacts) -> list[FinishedChunk]:
    end = chunk_end(chunk)
    if end is None or chunk.minted_at is None:
        return []
    items = [FinishedChunk(CursorKey.chunk_finished(end.at, chunk.chunk_id), chunk)]
    completed = completion_instant(chunk)
    if completed is not None:
        items.append(FinishedChunk(CursorKey.chunk_completed(completed, chunk.chunk_id), chunk, completion=True))
    return items


def select_window(
    facts: Iterable[StepFacts],
    since: CursorKey,
    until: datetime,
    frontier: datetime | None,
    limit: int,
    newest: datetime | None,
) -> TraceWindow:
    """The items after ``since`` and at or before ``until``, short of ``frontier``, first ``limit`` by key.

    Every item closing before ``frontier`` was read, so with no truncation the cursor may pass to it
    even when its rows closed nothing unsent — that is what keeps a saturated read from stalling. With
    neither, every closing fact through ``newest`` — the latest instant the read returned — was read, so the
    cursor passes that instant and the next read starts strictly after it."""
    closed: list[TraceItem] = []
    for chunk in facts:
        steps = identify_steps(chunk)
        for step in steps:
            if step.close is None or step.close.at > until:
                continue
            if frontier is not None and step.close.at >= frontier:
                continue
            key = CursorKey.of(step)
            if key > since:
                closed.append(ClosedStep(key, step, chunk, steps))
        for item in _chunk_items(chunk):
            if item.key.at > until or (frontier is not None and item.key.at >= frontier):
                continue
            if item.key > since:
                closed.append(item)
    closed.sort(key=lambda c: c.key)
    told = tuple(closed[:limit])
    if len(closed) > limit:
        return TraceWindow(told, told[-1].key)
    if frontier is not None:
        return TraceWindow(told, CursorKey.opening(frontier))
    if newest is not None:
        return TraceWindow(told, max(CursorKey.past(newest), since))
    return TraceWindow(told, told[-1].key if told else since)


def read_window(reads: IReadTraceSteps, since: CursorKey, until: datetime, limit: int) -> TraceWindow:
    """:func:`select_window` over the chunks holding a closing fact in ``[since.at, until]``."""
    candidates = reads.closing_candidates(since.at, until, limit)
    facts = reads.step_facts_for(candidates.chunk_ids)
    return select_window(facts.values(), since, until, candidates.frontier, limit, candidates.newest)


def assemble_window(window: TraceWindow) -> tuple[SpanRecord, ...]:
    """Every span of every item in the window — the one assembly a sweep and a replay both tell."""
    return tuple(span for item in window.items for span in _assemble(item))


def _assemble(item: TraceItem) -> tuple[SpanRecord, ...]:
    if isinstance(item, ClosedStep):
        return assemble_step(item.facts, item.step, item.steps)
    return (
        assemble_completion(item.facts)
        if item.completion
        else (*assemble_work(item.facts), *assemble_lifetime(item.facts))
    )


def oldest_unsent(reads: IReadTraceSteps, since: CursorKey, until: datetime) -> TraceItem | None:
    """The first item after ``since`` and at or before ``until``, or ``None``. Read an item at a
    time, moving past closing facts that close nothing."""
    while True:
        window = read_window(reads, since, until, 1)
        if window.items:
            return window.items[0]
        if window.position == since:
            return None
        since = window.position

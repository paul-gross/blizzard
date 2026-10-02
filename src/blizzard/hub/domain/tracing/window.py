"""The closed steps of a window — what a sweep tells after its cursor, and what a replay tells between two instants.

Contract: ``blizzard-product:/plans/tracing/fleet-spans/spec/emission.md`` §What a sweep does and §The cursor.
A step's close is known only from :func:`identify_steps` over its chunk's whole facts, so the window
reads candidate chunks by closing-fact time, hydrates them whole, and selects in the domain."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime

from blizzard.foundation.trace_spans import SpanRecord
from blizzard.hub.domain.tracing.assembly import assemble_step
from blizzard.hub.domain.tracing.cursor import CursorKey
from blizzard.hub.domain.tracing.facts import StepFacts
from blizzard.hub.domain.tracing.repository import IReadTraceSteps
from blizzard.hub.domain.tracing.steps import NodeStep, identify_steps


@dataclass(frozen=True)
class ClosedStep:
    key: CursorKey
    step: NodeStep
    facts: StepFacts


@dataclass(frozen=True)
class TraceWindow:
    """Up to a limit of closed steps in cursor order, and ``position`` — where a pass that told every
    one of them may move the cursor. ``position`` equals the window's ``since`` when nothing was proven."""

    steps: tuple[ClosedStep, ...]
    position: CursorKey


def select_window(
    facts: Iterable[StepFacts], since: CursorKey, until: datetime, frontier: datetime | None, limit: int
) -> TraceWindow:
    """The closed steps after ``since`` and at or before ``until``, short of ``frontier``, first ``limit`` by key.

    Every step closing before ``frontier`` was read, so with no truncation the cursor may pass to it
    even when its rows closed nothing unsent — that is what keeps a saturated read from stalling."""
    closed: list[ClosedStep] = []
    for chunk in facts:
        for step in identify_steps(chunk):
            if step.close is None or step.close.at > until:
                continue
            if frontier is not None and step.close.at >= frontier:
                continue
            key = CursorKey.of(step)
            if key > since:
                closed.append(ClosedStep(key, step, chunk))
    closed.sort(key=lambda c: c.key)
    told = tuple(closed[:limit])
    if len(closed) > limit:
        return TraceWindow(told, told[-1].key)
    if frontier is not None:
        return TraceWindow(told, CursorKey.opening(frontier))
    return TraceWindow(told, told[-1].key if told else since)


def read_window(reads: IReadTraceSteps, since: CursorKey, until: datetime, limit: int) -> TraceWindow:
    """:func:`select_window` over the chunks holding a closing fact in ``[since.at, until]``."""
    candidates = reads.closing_candidates(since.at, until, limit)
    facts = reads.step_facts_for(candidates.chunk_ids)
    return select_window(facts.values(), since, until, candidates.frontier, limit)


def assemble_window(window: TraceWindow) -> tuple[SpanRecord, ...]:
    """Every span of every step in the window — the one assembly a sweep and a replay both tell."""
    return tuple(span for closed in window.steps for span in assemble_step(closed.facts, closed.step))


def oldest_unsent(reads: IReadTraceSteps, since: CursorKey, until: datetime) -> ClosedStep | None:
    """The first step closed after ``since`` and at or before ``until``, or ``None``. Read a step at a
    time, moving past closing facts that close nothing."""
    while True:
        window = read_window(reads, since, until, 1)
        if window.steps:
            return window.steps[0]
        if window.position == since:
            return None
        since = window.position

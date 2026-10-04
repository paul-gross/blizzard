"""The one assembly of an egress row, shared by the live sweep and the backfill so the two can never disagree.

Contract: ``blizzard-product:/plans/fact-egress/steps/spec/rows.md``. Pure: facts in, a row and its date partition
out; no store, writer or cursor is touched."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime, timedelta

from blizzard.foundation.logging import get_logger
from blizzard.foundation.roles import dto
from blizzard.hub.domain.observability.egress.repository import EgressCheckpoint, EventsPosition, UsagePosition
from blizzard.hub.domain.observability.egress.rows import AttributedUsage, invocation_row, step_row
from blizzard.hub.domain.observability.egress.schema import (
    EVENTS_SCHEMA,
    INVOCATIONS_SCHEMA,
    STEPS_SCHEMA,
    invocation_egress_row,
    partition_of,
    step_egress_row,
)
from blizzard.hub.domain.observability.tracing.cursor import CursorKey
from blizzard.hub.domain.observability.tracing.facts import StepFacts
from blizzard.hub.domain.observability.tracing.steps import NodeStep, StepKind, identify_steps
from blizzard.hub.domain.observability.tracing.summary import summarize_step
from blizzard.hub.domain.observability.tracing.window import TraceWindow
from blizzard.hub.egress.writer import EgressFailure, EgressFailureCause, EgressValues

_log = get_logger("blizzard.hub.egress")

__all__ = [
    "InvocationRows",
    "StepsPassPlan",
    "add_step",
    "anchor_records",
    "guarded",
    "invocation_entry",
    "invocation_rows",
    "late_chunks",
    "plan_invocations_pass",
    "plan_steps_pass",
    "runner_step",
    "sorted_step_rows",
    "step_partition",
    "window_step_rows",
]

PartitionedRows = list[tuple[date, EgressValues]]
StepBatch = dict[str, tuple[CursorKey, EgressValues]]


def runner_step(steps: tuple[NodeStep, ...], epoch: int) -> NodeStep | None:
    """The runner step at ``epoch`` among a chunk's ``steps``, or ``None`` when it holds none."""
    return next((s for s in steps if s.kind is StepKind.RUNNER and s.epoch == epoch), None)


def add_step(
    batch: dict[str, tuple[CursorKey, EgressValues]],
    facts: StepFacts,
    steps: tuple[NodeStep, ...],
    step: NodeStep,
    key: CursorKey,
    now: datetime,
) -> None:
    """Add ``step``'s row to ``batch`` unless it is already there."""
    text = step.key.text()
    if text in batch:
        return
    summary = summarize_step(facts, step, steps)
    batch[text] = (key, step_egress_row(step_row(summary, now), key))


def invocation_entry(
    chunk: StepFacts, steps: tuple[NodeStep, ...], usage: AttributedUsage, now: datetime
) -> tuple[date, EgressValues] | None:
    """``usage``'s row and partition, or ``None`` when ``steps`` hold no runner step to attribute it to."""
    step = runner_step(steps, usage.fact.epoch)
    if step is None:
        return None
    invocation = invocation_row(chunk, step, usage, now)
    return partition_of(invocation.recorded_at), invocation_egress_row(invocation)


def ended_at(row: EgressValues) -> datetime:
    value = row.values["ended_at"]
    assert isinstance(value, datetime)
    return value


def step_partition(row: EgressValues) -> date:
    return partition_of(ended_at(row))


def guarded[T](call: Callable[[], T | EgressFailure]) -> T | EgressFailure:
    """A writer that raises instead of returning a failure is an I/O failure like any other."""
    try:
        return call()
    except Exception as error:
        _log.exception("egress writer raised")
        return EgressFailure(EgressFailureCause.IO_ERROR, f"{type(error).__name__}: {error}")


# --- a pass's plan, decided from what it read ---------------------------------------------------


def anchor_records(
    cursors: Mapping[str, EgressCheckpoint | None], now: datetime, settle: timedelta
) -> list[EgressCheckpoint]:
    """The cursor rows a first pass appends: when any dataset is unanchored, each unanchored one
    starts at ``now`` less the settle window and the pass writes no rows. Empty when every dataset
    is anchored and the pass may export."""
    if all(cursor is not None for cursor in cursors.values()):
        return []
    at = now - settle
    return [
        EgressCheckpoint(
            dataset,
            CursorKey.opening(at) if dataset == STEPS_SCHEMA.name else None,
            UsagePosition(at),
            0,
            (),
            now,
            EventsPosition(at) if dataset == EVENTS_SCHEMA.name else None,
        )
        for dataset, cursor in cursors.items()
        if cursor is None
    ]


def window_step_rows(window: TraceWindow, now: datetime) -> StepBatch:
    """The row of every step ``window`` proves closed, keyed by step."""
    batch: StepBatch = {}
    for closed in window.closed_steps():
        add_step(batch, closed.facts, closed.steps, closed.step, closed.key, now)
    return batch


def sorted_step_rows(batch: StepBatch) -> PartitionedRows:
    """``batch``'s rows in cursor order, each with its date partition."""
    return [(step_partition(row), row) for _, row in sorted(batch.values(), key=lambda entry: entry[0])]


def late_chunks(window: TraceWindow, usage: Sequence[AttributedUsage]) -> list[str]:
    """The chunks whose usage arrived but whose steps ``window`` does not hold — their facts must be
    read to re-tell the step the usage belongs to."""
    held = {closed.step.key.chunk_id for closed in window.closed_steps()}
    return sorted({row.chunk_id for row in usage if row.chunk_id not in held})


@dto
@dataclass(frozen=True)
class StepsPassPlan:
    """What a steps pass writes: ``rows`` in cursor order, the ``advanced`` cursor, and whether that
    cursor ``moved`` — a pass that writes nothing appends its cursor only when it moved."""

    rows: PartitionedRows
    advanced: EgressCheckpoint
    moved: bool


def plan_steps_pass(
    cursor: EgressCheckpoint,
    window: TraceWindow,
    usage: Sequence[AttributedUsage],
    late: Mapping[str, StepFacts],
    now: datetime,
) -> StepsPassPlan:
    """The rows of every step ``window`` proves closed, plus each step that late ``usage`` re-tells:
    its chunk is known, its runner step at the usage's epoch is closed, and its key is no later
    than the window's position. The step position advances to the window's; the usage position
    to the last usage read."""
    assert cursor.step is not None  # a steps cursor always carries its step position
    facts: dict[str, StepFacts] = {closed.step.key.chunk_id: closed.facts for closed in window.closed_steps()}
    steps: dict[str, tuple[NodeStep, ...]] = {
        closed.step.key.chunk_id: closed.steps for closed in window.closed_steps()
    }
    for chunk_id, held in late.items():
        if chunk_id not in facts:
            facts[chunk_id] = held
            steps[chunk_id] = identify_steps(held)
    batch = window_step_rows(window, now)
    for row in usage:
        chunk = facts.get(row.chunk_id)
        step = runner_step(steps[row.chunk_id], row.fact.epoch) if chunk is not None else None
        if chunk is None or step is None or step.close is None:
            continue
        key = CursorKey.of(step)
        if key <= window.position:
            add_step(batch, chunk, steps[row.chunk_id], step, key, now)
    position = usage[-1] if usage else None
    advanced = EgressCheckpoint(
        STEPS_SCHEMA.name,
        window.position,
        UsagePosition(position.fact.recorded_at, position.usage_id) if position is not None else cursor.usage,
        len(batch),
        (),
        now,
    )
    moved = advanced.step != cursor.step or advanced.usage != cursor.usage
    return StepsPassPlan(sorted_step_rows(batch), advanced, moved)


@dto
@dataclass(frozen=True)
class InvocationRows:
    """The rows a page of usage yields, and the usage ``skipped`` for holding no runner step."""

    rows: PartitionedRows
    skipped: tuple[AttributedUsage, ...]


def invocation_rows(usage: Sequence[AttributedUsage], facts: Mapping[str, StepFacts], now: datetime) -> InvocationRows:
    """Each usage's invocation row, attributed to its chunk's runner step at its epoch; usage whose
    chunk is unknown or holds no such step is skipped."""
    steps = {chunk_id: identify_steps(held) for chunk_id, held in facts.items()}
    rows: PartitionedRows = []
    skipped: list[AttributedUsage] = []
    for row in usage:
        chunk = facts.get(row.chunk_id)
        entry = invocation_entry(chunk, steps[row.chunk_id], row, now) if chunk is not None else None
        if entry is None:
            skipped.append(row)
        else:
            rows.append(entry)
    return InvocationRows(rows, tuple(skipped))


def plan_invocations_pass(
    usage: Sequence[AttributedUsage], facts: Mapping[str, StepFacts], now: datetime
) -> tuple[InvocationRows, EgressCheckpoint]:
    """A non-empty page of usage's rows and the cursor advanced to its last usage — appended even
    when every row was skipped, so skipped usage is never read again."""
    page = invocation_rows(usage, facts, now)
    last = usage[-1]
    return page, EgressCheckpoint(
        INVOCATIONS_SCHEMA.name, None, UsagePosition(last.fact.recorded_at, last.usage_id), len(page.rows), (), now
    )

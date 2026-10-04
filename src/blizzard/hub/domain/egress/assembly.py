"""The one assembly of an egress row, shared by the live sweep and the backfill so the two can never disagree.

Contract: ``blizzard-product:/plans/fact-egress/steps/spec/rows.md``. Pure: facts in, a row and its date partition
out; no store, writer or cursor is touched."""

from __future__ import annotations

from collections.abc import Callable
from datetime import date, datetime

from blizzard.foundation.logging import get_logger
from blizzard.hub.domain.egress.rows import AttributedUsage, invocation_row, step_row
from blizzard.hub.domain.egress.schema import invocation_egress_row, partition_of, step_egress_row
from blizzard.hub.domain.tracing.cursor import CursorKey
from blizzard.hub.domain.tracing.facts import StepFacts
from blizzard.hub.domain.tracing.steps import NodeStep, StepKind
from blizzard.hub.domain.tracing.summary import summarize_step
from blizzard.hub.egress.writer import EgressFailure, EgressFailureCause, EgressValues

_log = get_logger("blizzard.hub.egress")

__all__ = ["add_step", "guarded", "invocation_entry", "runner_step", "step_partition"]


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

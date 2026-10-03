"""The one assembly of an egress row, shared by the live sweep and the backfill so the two can never disagree.

Contract: ``blizzard-product:/plans/fact-egress/steps/spec/rows.md``. Pure: facts in, a row and its date partition
out; no store, writer or cursor is touched."""

from __future__ import annotations

from collections.abc import Callable
from datetime import date, datetime

from blizzard.foundation.logging import get_logger
from blizzard.hub.domain.egress.rows import UsageRow, invocation_row, step_row
from blizzard.hub.domain.egress.schema import invocation_egress_row, partition_of, step_egress_row
from blizzard.hub.domain.tracing.cursor import CursorKey
from blizzard.hub.domain.tracing.facts import StepFacts
from blizzard.hub.domain.tracing.steps import NodeStep, StepKind, identify_steps
from blizzard.hub.domain.tracing.summary import summarize_step
from blizzard.hub.egress.writer import EgressFailure, EgressFailureCause, EgressRow

_log = get_logger("blizzard.hub.egress")

__all__ = ["add_step", "guarded", "invocation_entry", "runner_step", "step_partition"]


def runner_step(facts: StepFacts, epoch: int) -> NodeStep | None:
    """The chunk's runner step at ``epoch``, or ``None`` when the facts hold none."""
    return next((s for s in identify_steps(facts) if s.kind is StepKind.RUNNER and s.epoch == epoch), None)


def add_step(
    batch: dict[str, tuple[CursorKey, EgressRow]],
    facts: StepFacts,
    step: NodeStep,
    key: CursorKey,
    now: datetime,
) -> None:
    """Add ``step``'s row to ``batch`` unless it is already there."""
    text = step.key.text()
    if text in batch:
        return
    summary = summarize_step(facts, step, identify_steps(facts))
    batch[text] = (key, step_egress_row(step_row(summary, now), key))


def invocation_entry(chunk: StepFacts, usage: UsageRow, now: datetime) -> tuple[date, EgressRow] | None:
    """``usage``'s row and partition, or ``None`` when its chunk holds no runner step to attribute it to."""
    if runner_step(chunk, usage.fact.epoch) is None:
        return None
    invocation = invocation_row(chunk, usage, now)
    return partition_of(invocation.recorded_at), invocation_egress_row(invocation)


def ended_at(row: EgressRow) -> datetime:
    value = row.values["ended_at"]
    assert isinstance(value, datetime)
    return value


def step_partition(row: EgressRow) -> date:
    return partition_of(ended_at(row))


def guarded[T](call: Callable[[], T | EgressFailure]) -> T | EgressFailure:
    """A writer that raises instead of returning a failure is an I/O failure like any other."""
    try:
        return call()
    except Exception as error:
        _log.exception("egress writer raised")
        return EgressFailure(EgressFailureCause.IO_ERROR, f"{type(error).__name__}: {error}")

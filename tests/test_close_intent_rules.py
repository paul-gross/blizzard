"""The close intent's own rules, pinned by value: the state each attempt's outcome leaves
an intent in, the terminal outcomes that derives, and the event a recorded outcome announces."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from blizzard.hub.domain.chunk.model import WorkItemCloseOutcome, WorkRef
from blizzard.hub.domain.work_items.closure import (
    TERMINAL_CLOSE_OUTCOMES,
    CloseEvent,
    CloseIntentState,
    close_event,
    close_intent_is_due,
)

pytestmark = pytest.mark.unit

_REF = WorkRef(source="forge", ref="12")


@pytest.mark.parametrize(
    ("outcome", "state"),
    [
        (WorkItemCloseOutcome.CLOSED, CloseIntentState.RETIRED),
        (WorkItemCloseOutcome.GONE, CloseIntentState.RETIRED),
        (WorkItemCloseOutcome.FAILED, CloseIntentState.BACKING_OFF),
    ],
)
def test_each_outcome_leaves_its_intent_in_one_declared_state(
    outcome: WorkItemCloseOutcome, state: CloseIntentState
) -> None:
    assert CloseIntentState.after(outcome) is state


def test_closed_and_gone_are_the_terminal_outcomes() -> None:
    assert frozenset({WorkItemCloseOutcome.CLOSED, WorkItemCloseOutcome.GONE}) == TERMINAL_CLOSE_OUTCOMES


def test_a_closed_ref_announces_work_item_closed() -> None:
    assert close_event("forge#12", WorkItemCloseOutcome.CLOSED, None) == CloseEvent(
        kind="work-item-closed", message="closed forge#12", detail=None
    )


@pytest.mark.parametrize("outcome", [WorkItemCloseOutcome.GONE, WorkItemCloseOutcome.FAILED])
def test_a_gone_or_failed_ref_announces_close_failed_with_its_outcome_and_reason(
    outcome: WorkItemCloseOutcome,
) -> None:
    assert close_event("forge#12", outcome, "boom") == CloseEvent(
        kind="work-item-close-failed",
        message="failed to close forge#12: boom",
        detail={"outcome": outcome.value, "reason": "boom"},
    )


_NOW = datetime(2026, 10, 29, tzinfo=UTC)


@pytest.mark.parametrize(
    ("attempt_count", "last_attempt_at"), [(0, None), (3, _NOW - timedelta(days=1))], ids=["fresh", "long-backed-off"]
)
def test_an_intent_is_held_until_its_newest_deferrals_resume_at(
    attempt_count: int, last_attempt_at: datetime | None
) -> None:
    resume_at = _NOW + timedelta(minutes=5)

    def due(now: datetime) -> bool:
        return close_intent_is_due(
            now, attempt_count=attempt_count, last_attempt_at=last_attempt_at, resume_at=resume_at
        )

    assert not due(resume_at - timedelta(microseconds=1))
    assert due(resume_at)


def test_a_passed_resume_at_leaves_the_backoff_to_decide() -> None:
    last = _NOW - timedelta(seconds=30)

    assert not close_intent_is_due(_NOW, attempt_count=1, last_attempt_at=last, resume_at=_NOW - timedelta(hours=1))
    assert close_intent_is_due(
        _NOW + timedelta(seconds=30), attempt_count=1, last_attempt_at=last, resume_at=_NOW - timedelta(hours=1)
    )

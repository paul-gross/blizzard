"""The close intent's own rules, pinned by value: the state each attempt's outcome leaves
an intent in, the terminal outcomes that derives, and the event a recorded outcome announces."""

from __future__ import annotations

import pytest

from blizzard.hub.domain.chunk.model import WorkItemCloseOutcome, WorkRef
from blizzard.hub.domain.work_items.closure import (
    TERMINAL_CLOSE_OUTCOMES,
    CloseEvent,
    CloseIntentState,
    close_event,
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
    assert close_event(_REF, WorkItemCloseOutcome.CLOSED, None) == CloseEvent(
        kind="work-item-closed", message="closed forge#12", detail=None
    )


@pytest.mark.parametrize("outcome", [WorkItemCloseOutcome.GONE, WorkItemCloseOutcome.FAILED])
def test_a_gone_or_failed_ref_announces_close_failed_with_its_outcome_and_reason(
    outcome: WorkItemCloseOutcome,
) -> None:
    assert close_event(_REF, outcome, "boom") == CloseEvent(
        kind="work-item-close-failed",
        message="failed to close forge#12: boom",
        detail={"outcome": outcome.value, "reason": "boom"},
    )

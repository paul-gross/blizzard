"""The flow queue's rules over one submission's standing (unit tier): the keyed lock a flow is declared under, when a
submission may close, and when it may take a turn — each clause pinned by value."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime

import pytest

from blizzard.hub.domain.delivery.flows import Flow, QueueStanding, Submission, flow_lock
from blizzard.hub.domain.kernel.locks import LockKey, LockNamespace

_AT = datetime(2026, 11, 13, 9, 0, tzinfo=UTC)
_MAIN = Flow(flow_id="flw_main", repository_id="repo_a", branch="main", created_at=_AT)
_OTHER = Flow(flow_id="flw_other", repository_id="repo_b", branch="master", created_at=_AT)
_SUB = Submission(
    submission_id="fsb_1",
    chunk_id="ch_1",
    node_id="nd_deliver",
    epoch=1,
    generation=0,
    arrival=3,
    submitted_at=_AT,
    flows=(_MAIN, _OTHER),
)
_FREE = QueueStanding(submission=_SUB, turning=False, busy=False, ahead=frozenset())
_NO_FLOWS = replace(_FREE, submission=replace(_SUB, flows=()))
_NONE: frozenset[str] = frozenset()


def test_a_flow_is_locked_under_its_resource() -> None:
    assert flow_lock(_MAIN) == LockKey(LockNamespace.FLOW, "repo:repo_a/main")


def test_a_standing_names_one_flow_lock_per_flow_its_submission_holds() -> None:
    assert _FREE.locks() == {flow_lock(_MAIN), flow_lock(_OTHER)}
    assert _NO_FLOWS.locks() == frozenset()


@pytest.mark.parametrize(
    ("standing", "may"),
    [
        (_FREE, True),
        (replace(_FREE, turning=True), False),
        (replace(_FREE, busy=True, ahead=frozenset({"fsb_0"})), True),
        (replace(_NO_FLOWS, turning=True), False),
    ],
    ids=["free", "turn-open", "busy-and-behind", "no-flows-turn-open"],
)
def test_a_submission_may_close_only_with_no_turn_open(standing: QueueStanding, may: bool) -> None:
    assert standing.may_close() is may


@pytest.mark.parametrize(
    ("standing", "passed_over", "may"),
    [
        (_FREE, _NONE, True),
        (replace(_FREE, submission=replace(_SUB, closed="stopped")), _NONE, False),
        (replace(_FREE, turning=True), _NONE, False),
        (replace(_FREE, busy=True), _NONE, False),
        (replace(_FREE, ahead=frozenset({"fsb_0"})), _NONE, False),
        (replace(_FREE, ahead=frozenset({"fsb_0"})), frozenset[str]({"fsb_0"}), True),
        (replace(_FREE, ahead=frozenset({"fsb_0", "fsb_2"})), frozenset[str]({"fsb_0"}), False),
        (_NO_FLOWS, _NONE, True),
        (replace(_NO_FLOWS, busy=True, ahead=frozenset({"fsb_0"})), _NONE, True),
        (replace(_NO_FLOWS, submission=replace(_NO_FLOWS.submission, closed="stopped")), _NONE, False),
        (replace(_NO_FLOWS, turning=True), _NONE, False),
    ],
    ids=[
        "free",
        "closed",
        "turn-open",
        "busy",
        "behind-an-eligible-arrival",
        "behind-only-passed-over",
        "behind-one-passed-over-and-one-not",
        "no-flows",
        "no-flows-ignores-busy-and-ahead",
        "no-flows-closed",
        "no-flows-turn-open",
    ],
)
def test_a_submission_may_take_a_turn_only_open_free_and_next(
    standing: QueueStanding, passed_over: frozenset[str], may: bool
) -> None:
    assert standing.may_take_turn(passed_over) is may


def test_a_flow_busy_with_a_closed_submissions_turn_refuses_the_turn() -> None:
    """``busy`` counts an open turn of any submission holding the flow, a closed one included — the standing read
    projects it so, and the rule refuses on it alone."""
    assert replace(_FREE, busy=True).may_take_turn(frozenset()) is False

"""The ask's rules, pinned by value: which asks are accepted, and which open asks a newer
unforwarded ask on the same lease supersedes."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from blizzard.runner.leases.asks import (
    ASK_TRANSITIONS,
    AskOnClosedLease,
    AskState,
    OpenAsk,
    ask_states,
    check_askable,
    newest_unforwarded,
    open_asks_of,
)
from blizzard.runner.leases.model import Lease
from blizzard.runner.leases.worker_lease import WorkerLease

pytestmark = pytest.mark.unit

_AT = datetime(2026, 7, 1, 12, 0, tzinfo=UTC)


def _lease() -> Lease:
    return Lease(
        lease_id="lease_1",
        chunk_id="ch_1",
        graph_id="gr_1",
        node_id="nd_build",
        node_name="build",
        epoch=1,
        retries_max=2,
        created_at=_AT,
    )


def _ask(question_id: str, *, lease_id: str = "lease_1", minutes: int = 0) -> OpenAsk:
    return OpenAsk(
        lease_id=lease_id,
        chunk_id="ch_1",
        question_id=question_id,
        question="q?",
        options=[],
        session_id=None,
        asked_at=_AT + timedelta(minutes=minutes),
    )


# --- check_askable ------------------------------------------------------------------------


def test_an_ask_on_the_active_lease_is_accepted() -> None:
    """Whatever the active lease is doing — parked on an earlier question or backing off, as a
    forced takeover's session finds it — the ask is accepted; the next resume parks it."""
    check_askable(WorkerLease(lease=_lease(), active=True))


def test_an_ask_on_a_takeovers_closed_reference_lease_is_refused() -> None:
    with pytest.raises(AskOnClosedLease, match="lease_1 is closed"):
        check_askable(WorkerLease(lease=_lease(), active=False))


# --- ask states ---------------------------------------------------------------------------


def test_only_the_newest_unforwarded_ask_per_lease_stays_open() -> None:
    newer, older = _ask("q_2", minutes=5), _ask("q_1")
    assert open_asks_of([newer, older], forwarded=[]) == [newer]


def test_forwarded_asks_are_never_superseded() -> None:
    unforwarded, parked = _ask("q_3", minutes=9), _ask("q_2", minutes=5)
    superseded = _ask("q_1")
    assert open_asks_of([unforwarded, parked, superseded], forwarded=["q_2"]) == [unforwarded, parked]


def test_a_superseded_ask_stays_superseded_once_the_newer_ask_is_forwarded_or_answered() -> None:
    newer, older = _ask("q_2", minutes=5), _ask("q_1")
    assert open_asks_of([newer, older], forwarded=["q_2"]) == [newer]
    assert open_asks_of([newer, older], forwarded=["q_2"], answered=["q_2"]) == []
    assert ask_states([newer, older], forwarded=["q_2"], answered=["q_2"]) == {
        "q_2": AskState.ANSWERED,
        "q_1": AskState.SUPERSEDED,
    }


def test_asks_on_different_leases_never_supersede_each_other() -> None:
    mine, theirs = _ask("q_2", minutes=5), _ask("q_1", lease_id="lease_2")
    assert open_asks_of([mine, theirs], forwarded=[]) == [mine, theirs]


def test_a_single_ask_is_kept_as_is() -> None:
    only = _ask("q_1")
    assert open_asks_of([only], forwarded=[]) == [only]
    assert open_asks_of([], forwarded=["q_1"]) == []


def test_only_the_newest_ask_is_forwarded_and_only_until_it_is() -> None:
    newer, older = _ask("q_2", minutes=5), _ask("q_1")
    assert newest_unforwarded([newer, older], forwarded=[]) == newer
    assert newest_unforwarded([newer, older], forwarded=["q_2"]) is None
    assert newest_unforwarded([], forwarded=[]) is None


_TIMELINES = (
    (("ask", "q_1"), ("ask", "q_2"), ("park", "q_2"), ("resume", "q_2")),
    (("ask", "q_1"), ("park", "q_1"), ("resume", "q_1"), ("ask", "q_2"), ("park", "q_2")),
    (("ask", "q_1"), ("park", "q_1"), ("ask", "q_2"), ("resume", "q_1"), ("park", "q_2")),
)


@pytest.mark.parametrize("timeline", _TIMELINES)
def test_each_derived_state_change_is_one_the_transition_table_declares(timeline: tuple[tuple[str, str], ...]) -> None:
    """The derivation agrees with :data:`ASK_TRANSITIONS`: as asks, parks, and resumes land, each ask
    moves only along a declared edge, and a superseded ask never leaves its state."""
    asks: list[OpenAsk] = []
    forwarded: set[str] = set()
    answered: set[str] = set()
    states: dict[str, AskState] = {}
    for minute, (event, question_id) in enumerate(timeline):
        if event == "ask":
            asks.insert(0, _ask(question_id, minutes=minute))
        (forwarded if event == "park" else answered if event == "resume" else set()).add(question_id)
        now = ask_states(asks, forwarded=forwarded, answered=answered)
        for asked, state in now.items():
            before = states.get(asked, AskState.UNFORWARDED)
            assert state == before or state in ASK_TRANSITIONS[before], (asked, before, state)
        states = now


def test_only_an_unforwarded_ask_can_be_superseded() -> None:
    assert AskState.SUPERSEDED in ASK_TRANSITIONS[AskState.UNFORWARDED]
    assert ASK_TRANSITIONS[AskState.FORWARDED] == frozenset({AskState.ANSWERED})
    assert ASK_TRANSITIONS[AskState.ANSWERED] == ASK_TRANSITIONS[AskState.SUPERSEDED] == frozenset()

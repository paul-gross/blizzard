"""The ask's rules, pinned by value: which asks are accepted, and which open asks a newer
unforwarded ask on the same lease supersedes."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from blizzard.runner.leases import Lease, WorkerLease
from blizzard.runner.leases.asks import (
    ASK_TRANSITIONS,
    AskOnClosedLease,
    AskState,
    OpenAsk,
    check_askable,
    unshadowed,
)

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
        runner_id="r1",
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


# --- unshadowed ---------------------------------------------------------------------------


def test_only_the_newest_unforwarded_ask_per_lease_stays_open() -> None:
    newer, older = _ask("q_2", minutes=5), _ask("q_1")
    assert unshadowed([newer, older], forwarded=[]) == [newer]


def test_forwarded_asks_are_never_shadowed() -> None:
    unforwarded, parked = _ask("q_3", minutes=9), _ask("q_2", minutes=5)
    shadowed = _ask("q_1")
    assert unshadowed([unforwarded, parked, shadowed], forwarded=["q_2"]) == [unforwarded, parked]


def test_asks_on_different_leases_never_shadow_each_other() -> None:
    mine, theirs = _ask("q_2", minutes=5), _ask("q_1", lease_id="lease_2")
    assert unshadowed([mine, theirs], forwarded=[]) == [mine, theirs]


def test_a_single_ask_is_kept_as_is() -> None:
    only = _ask("q_1")
    assert unshadowed([only], forwarded=[]) == [only]
    assert unshadowed([], forwarded=["q_1"]) == []


def test_only_an_unforwarded_ask_can_be_superseded() -> None:
    assert AskState.SUPERSEDED in ASK_TRANSITIONS[AskState.UNFORWARDED]
    assert ASK_TRANSITIONS[AskState.FORWARDED] == frozenset({AskState.ANSWERED})
    assert ASK_TRANSITIONS[AskState.ANSWERED] == ASK_TRANSITIONS[AskState.SUPERSEDED] == frozenset()

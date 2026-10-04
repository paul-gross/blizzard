"""The in-flight elicitation record's own rules, pinned by value — no store, no clock.

The staleness bound measured from the first launch, when a record still waits, the next
attempt index, and the declared table of which record writes are legal from which state.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from blizzard.runner.leases.elicitation import (
    ELICITATION_STALENESS_THRESHOLD,
    ElicitationNotRecorded,
    ElicitationState,
    ElicitationTransition,
    ElicitationVerb,
    PendingElicitation,
)

pytestmark = pytest.mark.unit

_LAUNCHED = datetime(2026, 7, 13, 12, 0, 0, tzinfo=UTC)


def _record(*, relaunch_count: int = 0, first_launched_at: datetime = _LAUNCHED) -> PendingElicitation:
    return PendingElicitation(
        id=1,
        lease_id="lease_1",
        epoch=1,
        pid=100,
        process_start_time="start-100",
        pgid=100,
        output_path="/tmp/lease_1.1.0.elicitation",
        first_launched_at=first_launched_at,
        relaunch_count=relaunch_count,
    )


def test_the_staleness_bound_is_fifteen_minutes() -> None:
    assert timedelta(minutes=15) == ELICITATION_STALENESS_THRESHOLD


def test_stale_measures_from_first_launch() -> None:
    record = _record()
    assert not record.stale(_LAUNCHED + ELICITATION_STALENESS_THRESHOLD - timedelta(seconds=1))
    assert not record.stale(_LAUNCHED + ELICITATION_STALENESS_THRESHOLD)
    assert record.stale(_LAUNCHED + ELICITATION_STALENESS_THRESHOLD + timedelta(seconds=1))


def test_a_relaunch_does_not_reset_the_bound() -> None:
    relaunched = _record(relaunch_count=3)
    assert relaunched.stale(_LAUNCHED + ELICITATION_STALENESS_THRESHOLD + timedelta(seconds=1))


def test_stale_reads_a_naive_first_launch_as_utc() -> None:
    naive = _record(first_launched_at=_LAUNCHED.replace(tzinfo=None))
    assert naive.stale(_LAUNCHED + ELICITATION_STALENESS_THRESHOLD + timedelta(seconds=1))
    assert not naive.stale(_LAUNCHED + timedelta(minutes=1))


@pytest.mark.parametrize(
    ("alive", "elapsed", "pending"),
    [
        (True, timedelta(minutes=1), True),
        (True, ELICITATION_STALENESS_THRESHOLD + timedelta(seconds=1), False),
        (False, timedelta(minutes=1), False),
        (False, ELICITATION_STALENESS_THRESHOLD + timedelta(seconds=1), False),
    ],
)
def test_pending_only_while_alive_and_under_bound(alive: bool, elapsed: timedelta, pending: bool) -> None:
    assert _record().pending(_LAUNCHED + elapsed, alive=alive) is pending


@pytest.mark.parametrize("relaunch_count", [0, 1, 4])
def test_next_attempt_is_count_plus_one(relaunch_count: int) -> None:
    assert _record(relaunch_count=relaunch_count).next_attempt == relaunch_count + 1


def test_state_of_a_record() -> None:
    assert ElicitationState.of(None) is ElicitationState.ABSENT
    assert ElicitationState.of(_record()) is ElicitationState.STANDING


@pytest.mark.parametrize(
    ("verb", "state", "transition"),
    [
        (ElicitationVerb.LAUNCH, ElicitationState.ABSENT, ElicitationTransition.APPLY),
        (ElicitationVerb.LAUNCH, ElicitationState.STANDING, ElicitationTransition.APPLY),
        (ElicitationVerb.STARTED, ElicitationState.ABSENT, ElicitationTransition.REFUSE),
        (ElicitationVerb.STARTED, ElicitationState.STANDING, ElicitationTransition.APPLY),
        (ElicitationVerb.RELAUNCH, ElicitationState.ABSENT, ElicitationTransition.REFUSE),
        (ElicitationVerb.RELAUNCH, ElicitationState.STANDING, ElicitationTransition.APPLY),
        (ElicitationVerb.CLEAR, ElicitationState.ABSENT, ElicitationTransition.NOOP),
        (ElicitationVerb.CLEAR, ElicitationState.STANDING, ElicitationTransition.APPLY),
    ],
)
def test_the_record_transition_table(
    verb: ElicitationVerb, state: ElicitationState, transition: ElicitationTransition
) -> None:
    assert state.on(verb) is transition


def test_launch_over_standing_restarts_bound() -> None:
    """A launch over a standing record is legal; the replacement's bound runs from its own
    launch, so a record that was stale is not stale once relaunched fresh."""
    assert ElicitationState.STANDING.on(ElicitationVerb.LAUNCH) is ElicitationTransition.APPLY
    later = _LAUNCHED + ELICITATION_STALENESS_THRESHOLD + timedelta(minutes=5)
    assert _record().stale(later)
    assert not _record(first_launched_at=later).stale(later + timedelta(minutes=1))


def test_started_on_absent_refused() -> None:
    assert ElicitationState.ABSENT.on(ElicitationVerb.STARTED) is ElicitationTransition.REFUSE
    refusal = ElicitationNotRecorded(ElicitationVerb.STARTED, "lease_1", 2)
    assert (refusal.verb, refusal.lease_id, refusal.epoch) == (ElicitationVerb.STARTED, "lease_1", 2)
    assert str(refusal) == "no in-flight elicitation for lease lease_1 epoch 2 to record started on"


def test_relaunch_on_absent_refused() -> None:
    assert ElicitationState.ABSENT.on(ElicitationVerb.RELAUNCH) is ElicitationTransition.REFUSE
    assert str(ElicitationNotRecorded(ElicitationVerb.RELAUNCH, "lease_1", 2)) == (
        "no in-flight elicitation for lease lease_1 epoch 2 to record relaunch on"
    )

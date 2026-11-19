"""``ExecutionObservationState.progress_with`` — the newer of the recorded and the reported progress."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta, timezone

import pytest

from blizzard.runner.leases.execution_observation import ExecutionObservationState

_OBSERVED = datetime(2026, 7, 14, 12, 0, tzinfo=UTC)
_EARLIER = _OBSERVED - timedelta(minutes=5)
_LATER = _OBSERVED + timedelta(minutes=5)


def _state(recorded: datetime | None) -> ExecutionObservationState:
    return ExecutionObservationState(last_observed_at=_OBSERVED, latest_progress_at=recorded)


@pytest.mark.parametrize(
    ("recorded", "reported", "newest"),
    [
        (None, _LATER, _LATER),
        (_EARLIER, None, _EARLIER),
        (_EARLIER, _LATER, _LATER),
        (_LATER, _EARLIER, _LATER),
        (None, None, None),
    ],
    ids=["reported-only", "recorded-only", "reported-newer", "recorded-newer", "neither"],
)
def test_progress_is_the_newer_of_the_recorded_and_the_reported(
    recorded: datetime | None, reported: datetime | None, newest: datetime | None
) -> None:
    assert _state(recorded).progress_with(reported) == newest


def test_a_reported_progress_in_another_offset_compares_as_utc() -> None:
    reported = _LATER.astimezone(timezone(timedelta(hours=-7)))

    assert _state(_EARLIER).progress_with(reported) == _LATER

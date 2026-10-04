"""The operator verbs' refusals and plans — trace replay, egress backfill, egress reset — pinned by value."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from blizzard.hub.domain.observability.egress.backfill import (
    BackfillUnavailable,
    BackfillWindowRefused,
    files_needed,
    require_backfillable,
)
from blizzard.hub.domain.observability.egress.repository import EgressCheckpoint, EventsPosition, UsagePosition
from blizzard.hub.domain.observability.egress.reset import (
    ResetRefused,
    ResetUnavailable,
    plan_reset,
    require_resettable,
)
from blizzard.hub.domain.observability.operator_window import OperatorWindow, WindowFault, fault_message
from blizzard.hub.domain.observability.tracing.cursor import CursorKey
from blizzard.hub.domain.observability.tracing.replay import (
    ReplayUnavailable,
    ReplayWindowRefused,
    require_replayable,
)

pytestmark = pytest.mark.unit

_NOW = datetime(2026, 7, 13, 12, tzinfo=UTC)
_HOUR = timedelta(hours=1)


# --- the shared window -------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("since", "until", "fault"),
    [
        (_NOW - _HOUR, _NOW - _HOUR, WindowFault.INVERTED),
        (_NOW - _HOUR, _NOW - 2 * _HOUR, WindowFault.INVERTED),
        (_NOW - 3 * _HOUR, _NOW - _HOUR + timedelta(seconds=1), WindowFault.TOO_WIDE),
        (_NOW - _HOUR / 2, _NOW + timedelta(seconds=1), WindowFault.FUTURE),
        (_NOW - 2 * _HOUR, _NOW + _HOUR, WindowFault.TOO_WIDE),
        (_NOW - _HOUR, _NOW, None),
    ],
)
def test_the_window_names_its_first_broken_bound(since: datetime, until: datetime, fault: WindowFault | None) -> None:
    assert OperatorWindow(since, until).fault(max_window=_HOUR, now=_NOW) is fault


def test_each_fault_reads_as_the_lanes_message() -> None:
    named = {"max_window_name": "replay_max_window", "max_window_seconds": 3600}
    assert fault_message(WindowFault.INVERTED, **named) == "until must be after since"  # type: ignore[arg-type]
    assert fault_message(WindowFault.TOO_WIDE, **named) == "window is wider than replay_max_window (3600 seconds)"  # type: ignore[arg-type]
    assert fault_message(WindowFault.FUTURE, **named) == "until must not be in the future"  # type: ignore[arg-type]


# --- trace replay ------------------------------------------------------------------------------


def test_a_replay_reaching_past_now_is_refused() -> None:
    with pytest.raises(ReplayWindowRefused, match="until must not be in the future"):
        require_replayable(
            _NOW - _HOUR,
            _NOW + timedelta(seconds=1),
            max_window_seconds=7200,
            now=_NOW,
            dry_run=True,
            exporter_wired=True,
        )


def test_a_replay_checks_the_window_before_the_exporter() -> None:
    with pytest.raises(ReplayWindowRefused):
        require_replayable(_NOW, _NOW, max_window_seconds=3600, now=_NOW, dry_run=False, exporter_wired=False)
    with pytest.raises(ReplayUnavailable):
        require_replayable(_NOW - _HOUR, _NOW, max_window_seconds=3600, now=_NOW, dry_run=False, exporter_wired=False)


def test_a_dry_replay_needs_no_exporter() -> None:
    require_replayable(_NOW - _HOUR, _NOW, max_window_seconds=3600, now=_NOW, dry_run=True, exporter_wired=False)


# --- egress backfill ---------------------------------------------------------------------------


def _backfillable(since: datetime, until: datetime, dataset: str | None = None, **over: object) -> None:
    args: dict[str, object] = {"datasets": ("steps", "invocations"), "max_window_seconds": 3600, "now": _NOW}
    args |= {"dry_run": False, "writers_wired": True} | over
    require_backfillable(since, until, dataset, **args)  # type: ignore[arg-type]


def test_a_wet_backfill_with_the_export_off_is_unavailable_before_any_window_check() -> None:
    with pytest.raises(BackfillUnavailable, match="not configured"):
        _backfillable(_NOW, _NOW, writers_wired=False)


def test_a_dry_backfill_runs_with_the_export_off() -> None:
    _backfillable(_NOW - _HOUR, _NOW, dry_run=True, writers_wired=False)


@pytest.mark.parametrize(
    ("since", "until", "dataset", "named"),
    [
        (_NOW, _NOW, "nope", "until must be after since"),
        (_NOW - 3 * _HOUR, _NOW - _HOUR + timedelta(seconds=1), "nope", "backfill_max_window"),
        (_NOW - _HOUR / 2, _NOW + timedelta(seconds=1), "nope", "until must not be in the future"),
        (_NOW - _HOUR, _NOW, "nope", "dataset 'nope' is not configured; the export writes steps, invocations"),
    ],
)
def test_a_backfill_refuses_the_window_before_the_dataset(
    since: datetime, until: datetime, dataset: str, named: str
) -> None:
    with pytest.raises(BackfillWindowRefused, match=named.replace("(", r"\(")):
        _backfillable(since, until, dataset)


def test_an_events_backfill_without_its_path_key_is_refused_after_the_window_and_before_the_dataset() -> None:
    with pytest.raises(BackfillWindowRefused, match="until must be after since"):
        _backfillable(_NOW, _NOW, "events", missing_path_key="BZ_EGRESS_PATH_KEY")
    with pytest.raises(BackfillWindowRefused, match="BZ_EGRESS_PATH_KEY"):
        _backfillable(_NOW - _HOUR, _NOW, "events", missing_path_key="BZ_EGRESS_PATH_KEY")
    _backfillable(_NOW - _HOUR, _NOW, "steps", missing_path_key="BZ_EGRESS_PATH_KEY")


def test_a_backfill_inside_its_bounds_is_allowed() -> None:
    _backfillable(_NOW - _HOUR, _NOW, "steps")
    _backfillable(_NOW - _HOUR, _NOW)


@pytest.mark.parametrize(
    ("partitions", "per_file", "files"),
    [([], 2, 0), ([1], 2, 1), ([2], 2, 1), ([3], 2, 2), ([3, 1, 4], 2, 5)],
)
def test_each_partition_splits_into_files_of_at_most_the_limit(
    partitions: list[int], per_file: int, files: int
) -> None:
    assert files_needed(partitions, per_file) == files


# --- egress reset ------------------------------------------------------------------------------


def test_a_reset_refuses_in_order_export_off_then_dataset_then_future() -> None:
    future = _NOW + timedelta(seconds=1)
    with pytest.raises(ResetUnavailable):
        require_resettable("nope", future, active=False, datasets=("steps",), now=_NOW)
    with pytest.raises(ResetRefused, match="dataset 'nope' is not configured"):
        require_resettable("nope", future, active=True, datasets=("steps",), now=_NOW)
    with pytest.raises(ResetRefused, match="to must not be in the future"):
        require_resettable("steps", future, active=True, datasets=("steps",), now=_NOW)
    require_resettable("steps", _NOW, active=True, datasets=("steps",), now=_NOW)


def test_an_events_reset_without_its_path_key_is_refused_after_the_export_check_and_before_the_dataset() -> None:
    with pytest.raises(ResetUnavailable):
        require_resettable("events", _NOW, active=False, datasets=(), now=_NOW, missing_path_key="BZ_EGRESS_PATH_KEY")
    with pytest.raises(ResetRefused, match="BZ_EGRESS_PATH_KEY"):
        require_resettable("events", _NOW, active=True, datasets=(), now=_NOW, missing_path_key="BZ_EGRESS_PATH_KEY")
    require_resettable("steps", _NOW, active=True, datasets=("steps",), now=_NOW, missing_path_key="BZ_EGRESS_PATH_KEY")


def _checkpoint(at: datetime) -> EgressCheckpoint:
    return EgressCheckpoint("invocations", None, UsagePosition(at), 0, (), at)


def test_a_reset_of_an_unanchored_dataset_measures_from_where_its_first_pass_would_anchor() -> None:
    settle = timedelta(minutes=5)
    checkpoint, result = plan_reset("invocations", None, _NOW - timedelta(minutes=4), now=_NOW, settle=settle)
    assert result.previous is None
    assert result.direction == "skipped"
    assert checkpoint == EgressCheckpoint("invocations", None, UsagePosition(_NOW - timedelta(minutes=4)), 0, (), _NOW)
    _, back = plan_reset("invocations", None, _NOW - timedelta(minutes=6), now=_NOW, settle=settle)
    assert back.direction == "repeated"


def test_a_reset_to_the_standing_position_is_a_repeat() -> None:
    standing = _NOW - _HOUR
    _, result = plan_reset("invocations", _checkpoint(standing), standing, now=_NOW, settle=timedelta(0))
    assert (result.previous, result.moved_to, result.direction) == (standing, standing, "repeated")


def test_a_steps_reset_moves_both_positions() -> None:
    to = _NOW - _HOUR
    checkpoint, result = plan_reset("steps", _checkpoint(_NOW - 2 * _HOUR), to, now=_NOW, settle=timedelta(0))
    assert checkpoint.step == CursorKey.opening(to)
    assert checkpoint.usage == UsagePosition(to)
    assert result.direction == "skipped"


def test_an_events_reset_moves_its_events_position_and_measures_from_where_it_stood() -> None:
    standing = _NOW - 2 * _HOUR
    cursor = EgressCheckpoint("events", None, UsagePosition(_NOW - 3 * _HOUR), 0, (), _NOW, EventsPosition(standing))
    to = _NOW - _HOUR
    checkpoint, result = plan_reset("events", cursor, to, now=_NOW, settle=timedelta(0))
    assert checkpoint == EgressCheckpoint("events", None, UsagePosition(to), 0, (), _NOW, EventsPosition(to))
    assert (result.previous, result.direction) == (standing, "skipped")

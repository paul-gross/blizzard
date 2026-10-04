"""What an egress pass writes, decided from what it read — pinned by value, with no store or clock."""

from __future__ import annotations

from datetime import timedelta

import pytest

from blizzard.hub.domain.observability.egress.assembly import (
    anchor_records,
    invocation_rows,
    late_chunks,
    plan_invocations_pass,
    plan_steps_pass,
)
from blizzard.hub.domain.observability.egress.repository import EgressCheckpoint, EventsPosition, UsagePosition
from blizzard.hub.domain.observability.egress.rows import AttributedUsage
from blizzard.hub.domain.observability.egress.status import egress_state
from blizzard.hub.domain.observability.lane_failure import failure_ongoing
from blizzard.hub.domain.observability.tracing.cursor import CursorKey
from blizzard.hub.domain.observability.tracing.window import TraceWindow
from tests.trace_fixtures import at, scenarios, usage

pytestmark = pytest.mark.unit

_NOW = at(3600)
_SETTLE = timedelta(seconds=60)


def _attributed(usage_id: int, chunk_id: str = "ch_1", epoch: int = 1, at_seconds: int = 14) -> AttributedUsage:
    return AttributedUsage(usage_id, chunk_id, "r-1", usage(epoch=epoch, at_seconds=at_seconds))


def _steps_cursor(at_seconds: int) -> EgressCheckpoint:
    return EgressCheckpoint("steps", CursorKey.opening(at(at_seconds)), UsagePosition(at(at_seconds)), 0, (), at(0))


# --- the first pass ----------------------------------------------------------------------------


def test_every_dataset_anchored_lets_the_pass_export() -> None:
    cursors = {
        "steps": _steps_cursor(0),
        "invocations": EgressCheckpoint("invocations", None, UsagePosition(at(0)), 0, (), at(0)),
    }
    assert anchor_records(cursors, _NOW, _SETTLE) == []


def test_any_unanchored_dataset_makes_the_pass_anchor_only_the_unanchored_ones() -> None:
    standing = _steps_cursor(0)
    anchors = anchor_records({"steps": standing, "invocations": None}, _NOW, _SETTLE)
    assert anchors == [EgressCheckpoint("invocations", None, UsagePosition(_NOW - _SETTLE), 0, (), _NOW)]
    both = anchor_records({"steps": None, "invocations": None}, _NOW, _SETTLE)
    assert [a.step for a in both] == [CursorKey.opening(_NOW - _SETTLE), None]


def test_an_unanchored_events_dataset_anchors_its_events_position_too() -> None:
    anchors = anchor_records({"steps": _steps_cursor(0), "events": None}, _NOW, _SETTLE)
    assert anchors == [
        EgressCheckpoint("events", None, UsagePosition(_NOW - _SETTLE), 0, (), _NOW, EventsPosition(_NOW - _SETTLE))
    ]


# --- steps -------------------------------------------------------------------------------------


def test_a_quiet_pass_writes_nothing_and_its_cursor_does_not_move() -> None:
    cursor = _steps_cursor(100)
    assert cursor.step is not None
    plan = plan_steps_pass(cursor, TraceWindow((), cursor.step), [], {}, _NOW)
    assert plan.rows == []
    assert plan.moved is False
    assert (plan.advanced.step, plan.advanced.usage, plan.advanced.row_count) == (cursor.step, cursor.usage, 0)


def test_late_usage_re_tells_its_closed_runner_step_behind_the_window_position() -> None:
    cursor = _steps_cursor(100)
    window = TraceWindow((), CursorKey.opening(at(1000)))
    late_usage = [_attributed(7), _attributed(8, chunk_id="ch_unknown")]
    assert late_chunks(window, late_usage) == ["ch_1", "ch_unknown"]

    plan = plan_steps_pass(cursor, window, late_usage, {"ch_1": scenarios()["runner-step"]}, _NOW)

    assert len(plan.rows) == 1
    assert plan.rows[0][1].values["chunk_id"] == "ch_1"
    assert plan.moved is True
    assert plan.advanced.step == window.position
    assert plan.advanced.usage == UsagePosition(at(14), 8)
    assert plan.advanced.row_count == 1


def test_late_usage_whose_step_lies_past_the_window_position_waits_for_the_window() -> None:
    cursor = _steps_cursor(0)
    window = TraceWindow((), CursorKey.opening(at(1)))
    plan = plan_steps_pass(cursor, window, [_attributed(7)], {"ch_1": scenarios()["runner-step"]}, _NOW)
    assert plan.rows == []
    assert plan.moved is True  # the usage position still advances past what was read


# --- invocations -------------------------------------------------------------------------------


def test_usage_with_no_runner_step_is_skipped_and_the_cursor_still_advances_past_it() -> None:
    page = [_attributed(1), _attributed(2, chunk_id="ch_unknown"), _attributed(3, epoch=9, at_seconds=20)]
    rows, advanced = plan_invocations_pass(page, {"ch_1": scenarios()["runner-step"]}, _NOW)
    assert len(rows.rows) == 1
    assert [u.usage_id for u in rows.skipped] == [2, 3]
    assert advanced == EgressCheckpoint("invocations", None, UsagePosition(at(20), 3), 1, (), _NOW)


def test_invocation_rows_attribute_each_usage_to_its_runner_step() -> None:
    rows = invocation_rows([_attributed(1)], {"ch_1": scenarios()["runner-step"]}, _NOW)
    assert rows.skipped == ()
    assert [row.values["chunk_id"] for _, row in rows.rows] == ["ch_1"]


# --- status ------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("configured", "rejected", "state"),
    [(False, False, "off"), (False, True, "off"), (True, True, "rejected"), (True, False, "on")],
)
def test_the_export_state(configured: bool, rejected: bool, state: str) -> None:
    assert egress_state(directory_configured=configured, rejected=rejected) == state


@pytest.mark.parametrize(
    ("failure", "latch", "ongoing"),
    [
        (None, "egress-write-failed", False),
        (object(), "egress-write-failed", True),
        (object(), "egress-write-recovered", False),
        (object(), None, False),
    ],
)
def test_a_failure_stands_until_a_recovery_follows_it(failure: object | None, latch: str | None, ongoing: bool) -> None:
    assert failure_ongoing(failure, latch, failed_kind="egress-write-failed") is ongoing

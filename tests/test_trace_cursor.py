"""Trace export cursor rules and window selection (unit tier) — pure, no store."""

from __future__ import annotations

from datetime import timedelta

import pytest

from blizzard.foundation.trace_export.cursor import (
    BACKOFF_CAP,
    CursorJump,
    JumpReason,
    backoff_delay,
    first_pass_jump,
    lag_cap_jump,
)
from blizzard.hub.domain.tracing.cursor import CursorKey
from blizzard.hub.domain.tracing.facts import ChunkStoppedRecord, EpochOwnerRecord, LeaseRecord, StepFacts
from blizzard.hub.domain.tracing.steps import identify_steps
from blizzard.hub.domain.tracing.window import select_window
from tests import trace_fixtures as fx

pytestmark = pytest.mark.unit

_LAG = timedelta(hours=24)


# --- ordering key ----------------------------------------------------------------------


def test_key_orders_by_time_then_chunk_then_epoch_then_decision() -> None:
    keys = [
        CursorKey(fx.at(2), "ch_a", 1),
        CursorKey(fx.at(1), "ch_b", 9, "dec_z"),
        CursorKey(fx.at(1), "ch_b", 2, "dec_a"),
        CursorKey(fx.at(1), "ch_b", 2),
        CursorKey(fx.at(1), "ch_a", 3),
    ]
    assert sorted(keys) == [
        CursorKey(fx.at(1), "ch_a", 3),
        CursorKey(fx.at(1), "ch_b", 2),
        CursorKey(fx.at(1), "ch_b", 2, "dec_a"),
        CursorKey(fx.at(1), "ch_b", 9, "dec_z"),
        CursorKey(fx.at(2), "ch_a", 1),
    ]


def test_opening_sorts_before_every_step_closing_at_that_instant() -> None:
    assert CursorKey.opening(fx.at(5)) < CursorKey(fx.at(5), "ch_a", 0)
    assert CursorKey.opening(fx.at(5)) > CursorKey(fx.at(4), "ch_z", 99, "dec_z")


def test_key_of_a_closed_step_uses_its_closing_fact_time() -> None:
    facts = fx.make_facts(**fx.merge(fx.runner_epoch(1, 10)), transitions=(fx.to("g1", "review", 30, 1),))
    (step,) = identify_steps(facts)
    assert CursorKey.of(step) == CursorKey(fx.at(30), "ch_1", 1, "")


def test_key_of_an_open_step_is_refused() -> None:
    (step,) = identify_steps(fx.make_facts(**fx.runner_epoch(1, 10)))
    with pytest.raises(ValueError):
        CursorKey.of(step)


# --- start and enable-after-gap --------------------------------------------------------


def test_first_pass_with_no_cursor_starts_at_now() -> None:
    assert first_pass_jump(None, fx.at(100), _LAG, key=CursorKey) == CursorJump(
        JumpReason.START, CursorKey.opening(fx.at(100))
    )


def test_first_pass_over_a_cursor_older_than_the_lag_jumps_to_now_and_names_the_window() -> None:
    now = fx.at(0) + timedelta(days=3)
    stale = CursorKey(fx.at(0), "ch_1", 1)
    assert first_pass_jump(stale, now, _LAG, key=CursorKey) == CursorJump(
        JumpReason.ENABLE_AFTER_GAP, CursorKey.opening(now), skipped_from=stale
    )


def test_first_pass_over_a_recent_cursor_keeps_it() -> None:
    assert first_pass_jump(CursorKey(fx.at(0)), fx.at(0) + _LAG, _LAG, key=CursorKey) is None


# --- lag cap ---------------------------------------------------------------------------


def test_lag_cap_jumps_to_the_boundary_when_the_oldest_unsent_step_is_past_it() -> None:
    now = fx.at(0) + timedelta(days=2)
    cursor = CursorKey(fx.at(0), "ch_1", 1)
    assert lag_cap_jump(cursor, fx.at(60), now, _LAG) == CursorJump(
        JumpReason.LAG_CAP, CursorKey.opening(now - _LAG), skipped_from=cursor
    )


def test_an_idle_stale_cursor_never_jumps() -> None:
    now = fx.at(0) + timedelta(days=30)
    assert lag_cap_jump(CursorKey(fx.at(0)), None, now, _LAG) is None


def test_an_unsent_step_inside_the_lag_does_not_jump() -> None:
    now = fx.at(0) + timedelta(days=2)
    assert lag_cap_jump(CursorKey(fx.at(0)), now - _LAG, now, _LAG) is None


# --- backoff ---------------------------------------------------------------------------


def test_backoff_doubles_from_the_sweep_interval_up_to_the_cap() -> None:
    every = timedelta(seconds=60)
    delays = [backoff_delay(n, every) for n in range(1, 8)]
    assert delays == [
        timedelta(seconds=60),
        timedelta(seconds=120),
        timedelta(seconds=240),
        timedelta(seconds=480),
        BACKOFF_CAP,
        BACKOFF_CAP,
        BACKOFF_CAP,
    ]
    assert timedelta(minutes=10) == BACKOFF_CAP


def test_backoff_survives_a_very_long_outage() -> None:
    assert backoff_delay(10_000, timedelta(seconds=60)) == BACKOFF_CAP


def test_backoff_with_no_failure_is_the_sweep_interval() -> None:
    assert backoff_delay(0, timedelta(seconds=60)) == timedelta(seconds=60)


# --- window selection ------------------------------------------------------------------


def _chunk(chunk_id: str, *, closes_at: int) -> StepFacts:
    """One runner step on epoch 1, closed by a stop at ``closes_at``."""
    return StepFacts(
        chunk_id=chunk_id,
        graphs=fx.GRAPHS,
        pin_graph_id="g1",
        lease_facts=(LeaseRecord(1, fx.at(1)),),
        epoch_owners=(EpochOwnerRecord(1, "r-1", fx.at(0)),),
        chunk_stopped=(ChunkStoppedRecord(fx.at(closes_at)),),
    )


def _ids(window) -> list[str]:  # type: ignore[no-untyped-def]
    return [c.key.chunk_id for c in window.steps]


def test_window_keeps_steps_strictly_after_since_and_at_or_before_until() -> None:
    facts = [_chunk("ch_a", closes_at=10), _chunk("ch_b", closes_at=10), _chunk("ch_c", closes_at=20)]
    since = CursorKey(fx.at(10), "ch_a", 1)
    window = select_window(facts, since, fx.at(20), None, 10)
    assert _ids(window) == ["ch_b", "ch_c"]
    assert window.position == CursorKey(fx.at(20), "ch_c", 1)
    assert _ids(select_window(facts, since, fx.at(19), None, 10)) == ["ch_b"]


def test_window_takes_the_limit_in_key_order_and_positions_at_the_last_told() -> None:
    facts = [_chunk("ch_c", closes_at=5), _chunk("ch_a", closes_at=5), _chunk("ch_b", closes_at=3)]
    window = select_window(facts, CursorKey.opening(fx.at(0)), fx.at(10), None, 2)
    assert _ids(window) == ["ch_b", "ch_a"]
    assert window.position == CursorKey(fx.at(5), "ch_a", 1)


def test_window_holds_back_steps_at_or_past_the_frontier_and_may_pass_to_it() -> None:
    facts = [_chunk("ch_a", closes_at=5), _chunk("ch_b", closes_at=8)]
    window = select_window(facts, CursorKey.opening(fx.at(0)), fx.at(10), fx.at(8), 10)
    assert _ids(window) == ["ch_a"]
    assert window.position == CursorKey.opening(fx.at(8))


def test_window_with_nothing_closed_and_no_frontier_stays_put() -> None:
    since = CursorKey(fx.at(3), "ch_a", 1)
    window = select_window([fx.make_facts(**fx.runner_epoch(1, 10))], since, fx.at(20), None, 10)
    assert window.steps == ()
    assert window.position == since

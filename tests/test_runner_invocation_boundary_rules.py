"""Pure rules of the transcript invocation boundary — its transition table, the one
worker-starting boundary per generation, and the kind a spawn opens. No store, no clock."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from blizzard.runner.transcripts.invocation_boundaries import (
    BOUNDARY_TRANSITIONS,
    InvocationBoundary,
    InvocationBoundaryKind,
    boundary_transition_applies,
    spawn_boundary_kind,
    worker_boundary_open,
)

pytestmark = pytest.mark.unit

_T0 = datetime(2026, 1, 1, tzinfo=UTC)


def _boundary(
    *, generation: int = 1, kind: InvocationBoundaryKind = "spawn", closed: bool = False
) -> InvocationBoundary:
    return InvocationBoundary(
        lease_id="lease_1",
        chunk_id="ch_1",
        node_id="nd_build",
        epoch=1,
        generation=generation,
        kind=kind,
        start_position=None,
        opened_at=_T0,
        closed_at=_T0 if closed else None,
        closed_reason="completed" if closed else None,
    )


def test_boundary_transition_table() -> None:
    assert {
        "absent": frozenset({"open"}),
        "open": frozenset({"advance", "close"}),
        "closed": frozenset(),
    } == BOUNDARY_TRANSITIONS


@pytest.mark.parametrize(
    ("boundary", "transition", "applies"),
    [
        (None, "open", True),
        (None, "advance", False),
        (_boundary(), "open", False),
        (_boundary(), "advance", True),
        (_boundary(), "close", True),
    ],
)
def test_transition_applies_by_state(boundary: InvocationBoundary | None, transition: str, applies: bool) -> None:
    assert boundary_transition_applies(boundary, transition) is applies  # type: ignore[arg-type]


def test_open_after_close_is_noop() -> None:
    assert boundary_transition_applies(_boundary(closed=True), "open") is False


def test_advance_closed_boundary_is_noop() -> None:
    assert boundary_transition_applies(_boundary(closed=True), "advance") is False


def test_worker_boundary_open() -> None:
    nudge = _boundary(generation=2, kind="nudge")
    judge = _boundary(generation=3, kind="judge")
    closed = _boundary(generation=4, kind="resume", closed=True)
    boundaries = [nudge, judge, closed]

    assert worker_boundary_open(boundaries, 2) is True
    assert worker_boundary_open(boundaries, 3) is False
    assert worker_boundary_open(boundaries, 4) is False
    assert worker_boundary_open([], 1) is False


def test_spawn_boundary_kind() -> None:
    assert spawn_boundary_kind(resumed=True) == "resume"
    assert spawn_boundary_kind(resumed=False) == "spawn"

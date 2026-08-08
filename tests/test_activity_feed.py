"""``ActivityFeed`` (unit tier) — the pure merge/sort/cap behind the board's
Event log page-load backfill (issue #213). Built from already-loaded
:class:`ActivityRow`/:class:`EventRow` literals — no store; the per-source bounded reads
are exercised at the component tier (``tests/test_activity_feed_store.py``).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from blizzard.hub.domain.work import ActivityFeed, ActivityRow, EventRow

pytestmark = pytest.mark.unit

_T0 = datetime(2026, 1, 1, tzinfo=UTC)


def _at(seconds: int) -> datetime:
    return _T0 + timedelta(seconds=seconds)


def _chunk_changed(key: str, *, at: datetime, cause: str = "claimed", chunk_id: str = "ch_1") -> ActivityRow:
    return ActivityRow(type="chunk-changed", key=key, at=at, chunk_id=chunk_id, cause=cause)


def _event(id_: int, *, at: datetime) -> EventRow:
    return EventRow(
        id=id_,
        recorded_at=at,
        severity="info",
        kind="some.kind",
        runner_id="runner-a",
        chunk_id=None,
        lease_id=None,
        node_name=None,
        message="m",
        detail=None,
    )


def _runner_changed(key: str, *, at: datetime) -> ActivityRow:
    return ActivityRow(type="runner-changed", key=key, at=at, runner_id="runner-a", kind="paused")


def test_empty_source_set_returns_empty_list() -> None:
    assert ActivityFeed.of([], [], [], limit=200).rows == []


def test_merges_all_three_sources() -> None:
    feed = ActivityFeed.of(
        [_chunk_changed("route_created:r1", at=_at(0))],
        [_event(1, at=_at(1))],
        [_runner_changed("runner_pause_facts:1", at=_at(2))],
        limit=200,
    ).rows
    assert {row.type for row in feed} == {"chunk-changed", "event-logged", "runner-changed"}
    assert len(feed) == 3


def test_sorts_by_at_descending() -> None:
    early = _chunk_changed("chunks:ch_1", at=_at(0))
    late = _chunk_changed("chunks:ch_2", at=_at(10))
    feed = ActivityFeed.of([early, late], [], [], limit=200).rows
    assert [row.key for row in feed] == ["chunks:ch_2", "chunks:ch_1"]


def test_ties_on_at_break_by_key_descending() -> None:
    same_instant_a = _chunk_changed("chunks:ch_a", at=_T0)
    same_instant_b = _chunk_changed("chunks:ch_b", at=_T0)
    feed = ActivityFeed.of([same_instant_a, same_instant_b], [], [], limit=200).rows
    # "chunks:ch_b" > "chunks:ch_a" lexicographically — desc puts b first.
    assert [row.key for row in feed] == ["chunks:ch_b", "chunks:ch_a"]


def test_caps_to_limit_keeping_the_newest() -> None:
    rows = [_chunk_changed(f"chunks:ch_{i}", at=_at(i)) for i in range(5)]
    feed = ActivityFeed.of(rows, [], [], limit=2).rows
    assert [row.key for row in feed] == ["chunks:ch_4", "chunks:ch_3"]


def test_event_row_reshapes_into_an_event_logged_activity_row() -> None:
    feed = ActivityFeed.of([], [_event(7, at=_at(3))], [], limit=200).rows
    assert feed == [
        ActivityRow(
            type="event-logged",
            key="event_log:7",
            at=_at(3),
            chunk_id=None,
            runner_id="runner-a",
            severity="info",
            kind="some.kind",
        )
    ]

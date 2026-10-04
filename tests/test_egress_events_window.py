"""The ``events`` window and schema (unit tier) — the merge of markers and drops in cursor order, the cut on an item
boundary, and the schema's column parity with :class:`ExportedEventsEntry`."""

from __future__ import annotations

from dataclasses import fields
from datetime import UTC, datetime, timedelta

import pytest

from blizzard.hub.domain.observability.analytics.events import DerivationMarker, DropFact
from blizzard.hub.domain.observability.egress.event_rows import ExportedEventsEntry
from blizzard.hub.domain.observability.egress.events_window import position_of, take
from blizzard.hub.domain.observability.egress.repository import EventsPosition
from blizzard.hub.domain.observability.egress.schema import EVENTS_SCHEMA, events_position_text

pytestmark = pytest.mark.unit

_T0 = datetime(2026, 8, 12, tzinfo=UTC)


def _at(seconds: int) -> datetime:
    return _T0 + timedelta(seconds=seconds)


def _marker(seconds: int, segment_id: str, events: int = 0, version: str = "v1") -> DerivationMarker:
    return DerivationMarker(segment_id, version, "fp", _at(seconds), events, True)


def _drop(seconds: int, segment_id: str) -> DropFact:
    return DropFact(segment_id, "ch_1", 1, 1, _at(seconds))


def _ids(items: tuple[DerivationMarker | DropFact, ...]) -> list[str]:
    return [f"{'drop' if isinstance(item, DropFact) else 'derivation'}:{item.segment_id}" for item in items]


def test_the_events_schema_names_every_events_row_field_in_order() -> None:
    assert [column.name for column in EVENTS_SCHEMA.columns] == [f.name for f in fields(ExportedEventsEntry)]
    assert (EVENTS_SCHEMA.name, EVENTS_SCHEMA.major_version) == ("events", 1)


def test_a_drop_sorts_before_a_derivation_at_its_own_time_and_segment() -> None:
    assert position_of(_drop(5, "sg_a")) < position_of(_marker(5, "sg_a"))
    assert position_of(_drop(5, "sg_a")) == EventsPosition(_at(5), "sg_a", "")
    assert events_position_text(position_of(_drop(5, "sg_a"))) < events_position_text(position_of(_marker(5, "sg_a")))


def test_markers_and_drops_merge_in_cursor_order() -> None:
    markers = [_marker(1, "sg_a"), _marker(5, "sg_a"), _marker(9, "sg_c")]
    drops = [_drop(5, "sg_a"), _drop(7, "sg_b")]

    assert _ids(take(markers, drops, 100, read_limit=100)) == [
        "derivation:sg_a",
        "drop:sg_a",
        "derivation:sg_a",
        "drop:sg_b",
        "derivation:sg_c",
    ]


def test_the_cut_counts_rows_and_never_splits_a_derivation() -> None:
    markers = [_marker(1, "sg_a", events=2), _marker(2, "sg_b", events=2), _marker(3, "sg_c")]

    assert [m.segment_id for m in take(markers, [], 6, read_limit=100)] == ["sg_a", "sg_b"]
    assert [m.segment_id for m in take(markers, [], 5, read_limit=100)] == ["sg_a"]


def test_a_derivation_larger_than_the_limit_is_taken_alone() -> None:
    markers = [_marker(1, "sg_a", events=9), _marker(2, "sg_b")]

    assert [m.segment_id for m in take(markers, [_drop(3, "sg_c")], 2, read_limit=100)] == ["sg_a"]


def test_nothing_past_a_full_read_is_taken_from_the_other_source() -> None:
    markers = [_marker(1, "sg_a"), _marker(4, "sg_b")]
    drops = [_drop(2, "sg_x"), _drop(6, "sg_y")]

    taken = take(markers, drops, 100, read_limit=2)

    # Both reads came back full: a marker could lie between sg_b and the drop at 6.
    assert [item.segment_id for item in taken] == ["sg_a", "sg_x", "sg_b"]


def test_nothing_read_takes_nothing() -> None:
    assert take([], [], 10, read_limit=10) == ()

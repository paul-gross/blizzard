"""The ``events`` egress reads (component tier) — markers and drops past a position, whole derivations, the
backfill's epoch selection, and the events cursor's persistence, over a real migrated store."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy import Engine

from blizzard.foundation.store.engine import create_engine_from_url
from blizzard.hub.config import HubConfig
from blizzard.hub.domain.analytics.events import SegmentProvenance, TranscriptEvent
from blizzard.hub.domain.egress.repository import EgressCheckpoint, EpochKey, EventsPosition, UsagePosition
from blizzard.hub.domain.transcripts import TranscriptSlice
from blizzard.hub.runtime import migration_runner
from blizzard.hub.store.internal.egress_event_store import EgressEventStore
from blizzard.hub.store.internal.egress_store import EgressStore
from blizzard.hub.store.internal.transcript_event_store import TranscriptEventStore
from blizzard.hub.store.internal.transcript_segment_store import TranscriptSegmentStore
from tests.support import count_queries, hub_store_connections, seed_chunk, seed_graph, seed_lease

pytestmark = pytest.mark.component

_T0 = datetime(2026, 8, 12, tzinfo=UTC)
_V1 = "blizzard-analytics/1"
_V2 = "blizzard-analytics/2"
_PROVENANCE = SegmentProvenance("claude_code", "1.0", "claude-sonnet-5", "high")


def _at(seconds: float) -> datetime:
    return _T0 + timedelta(seconds=seconds)


def _engine(tmp_path: Path, chunk_ids: tuple[str, ...] = ("ch_1", "ch_2")) -> Engine:
    db_url = f"sqlite:///{tmp_path / 'hub.db'}"
    migration_runner(HubConfig(root=tmp_path, db_url=db_url)).upgrade("head")
    engine = create_engine_from_url(db_url)
    with engine.begin() as conn:
        seed_graph(conn, "gr_1", at=_T0)
        for chunk_id in chunk_ids:
            seed_chunk(conn, chunk_id, graph_id="gr_1", at=_T0)
    return engine


def _segment(
    engine: Engine, segment_id: str, chunk_id: str = "ch_1", *, epoch: int = 1, cwd: str | None = None
) -> None:
    record = TranscriptSlice(
        segment_id=segment_id,
        chunk_id=chunk_id,
        node_id="nd_build",
        epoch=epoch,
        spawn_generation=2,
        runner_id="r1",
        turn_range_start=0,
        turn_range_end=0,
        final=True,
        normalizer_version="claude-code-jsonl/2",
        harness_version="claude-code-1.0",
        record_truncated=False,
        turns_json=json.dumps([]),
        spawn_cwd=cwd,
    )
    TranscriptSegmentStore(hub_store_connections(engine)).insert_accepted(record, byte_count=1, codec="zlib", at=_T0)


def _event(chunk_id: str, turn: int, *, epoch: int = 1) -> TranscriptEvent:
    return TranscriptEvent(
        kind="file_read",
        turn_path=str(turn),
        occurrence=0,
        payload="{}",
        subject=f"/w/f{turn}.py",
        tool="Read",
        chunk_id=chunk_id,
        node_id="nd_build",
        epoch=epoch,
        spawn_generation=2,
        graph_id="gr_1",
        depth=0,
        agent_type=None,
        occurred_at=None,
    )


def _derive(
    engine: Engine, segment_id: str, at: datetime, *, events: int = 1, version: str = _V1, chunk_id: str = "ch_1"
) -> None:
    TranscriptEventStore(hub_store_connections(engine)).replace_segment_events(
        segment_id,
        version,
        [_event(chunk_id, turn) for turn in range(events)],
        complete=True,
        content_fingerprint="fp",
        at=at,
        provenance=_PROVENANCE,
    )


def _drop(engine: Engine, segment_id: str, at: datetime) -> None:
    TranscriptEventStore(hub_store_connections(engine)).drop_segments(frozenset({segment_id}), at=at)


def _store(engine: Engine) -> EgressEventStore:
    return EgressEventStore(hub_store_connections(engine))


def test_markers_after_a_position_come_in_cursor_order_settled_and_limited(tmp_path: Path) -> None:
    engine = _engine(tmp_path)
    for segment_id in ("sg_a", "sg_b", "sg_c"):
        _segment(engine, segment_id)
    _derive(engine, "sg_b", _at(10))
    _derive(engine, "sg_a", _at(10))
    _derive(engine, "sg_a", _at(10), version=_V2)
    _derive(engine, "sg_c", _at(30))
    store = _store(engine)

    def keys(position: EventsPosition, until: datetime, limit: int, version: str | None = None) -> list[tuple]:
        markers = store.markers_after(position, until, limit, extractor_version=version)
        return [(m.derived_at, m.segment_id, m.extractor_version) for m in markers]

    assert keys(EventsPosition(_at(0)), _at(60), 10) == [
        (_at(10), "sg_a", _V1),
        (_at(10), "sg_a", _V2),
        (_at(10), "sg_b", _V1),
        (_at(30), "sg_c", _V1),
    ]
    assert keys(EventsPosition(_at(10), "sg_a", _V1), _at(60), 10)[0] == (_at(10), "sg_a", _V2)
    assert keys(EventsPosition(_at(0)), _at(20), 10)[-1] == (_at(10), "sg_b", _V1)  # sg_c is not settled
    assert len(keys(EventsPosition(_at(0)), _at(60), 2)) == 2
    assert keys(EventsPosition(_at(0)), _at(60), 10, _V2) == [(_at(10), "sg_a", _V2)]


def test_drops_after_a_position_come_in_cursor_order_settled_and_limited(tmp_path: Path) -> None:
    engine = _engine(tmp_path)
    for segment_id in ("sg_a", "sg_b", "sg_c"):
        _segment(engine, segment_id)
    _drop(engine, "sg_b", _at(10))
    _drop(engine, "sg_a", _at(10))
    _drop(engine, "sg_c", _at(30))
    store = _store(engine)

    def keys(position: EventsPosition, until: datetime, limit: int) -> list[tuple[datetime, str]]:
        return [(d.dropped_at, d.segment_id) for d in store.drops_after(position, until, limit)]

    assert keys(EventsPosition(_at(0)), _at(60), 10) == [(_at(10), "sg_a"), (_at(10), "sg_b"), (_at(30), "sg_c")]
    assert keys(EventsPosition(_at(10), "sg_a", ""), _at(60), 10) == [(_at(10), "sg_b"), (_at(30), "sg_c")]
    # A derivation position at the drop's own time and segment is already past the drop.
    assert keys(EventsPosition(_at(10), "sg_b", _V1), _at(60), 10) == [(_at(30), "sg_c")]
    assert keys(EventsPosition(_at(0)), _at(20), 10) == [(_at(10), "sg_a"), (_at(10), "sg_b")]
    assert keys(EventsPosition(_at(0)), _at(60), 1) == [(_at(10), "sg_a")]
    drop = store.drops_after(EventsPosition(_at(0)), _at(60), 1)[0]
    assert (drop.chunk_id, drop.epoch, drop.spawn_generation) == ("ch_1", 1, 2)


def test_a_derivation_carries_its_marker_events_provenance_and_segment_identity(tmp_path: Path) -> None:
    engine = _engine(tmp_path)
    _segment(engine, "sg_a", cwd="/w")
    _segment(engine, "sg_e")
    _derive(engine, "sg_a", _at(10), events=2)
    _derive(engine, "sg_e", _at(11), events=0)
    store = _store(engine)
    markers = store.markers_after(EventsPosition(_at(0)), _at(60), 10, extractor_version=None)

    full, empty = store.derivations(markers)

    assert full.marker == markers[0]
    assert [event.turn_path for event in full.events] == ["0", "1"]
    assert full.provenance == _PROVENANCE
    assert (full.chunk_id, full.epoch, full.spawn_generation, full.spawn_cwd) == ("ch_1", 1, 2, "/w")
    assert empty.events == ()
    assert empty.marker.event_count == 0
    assert empty.spawn_cwd is None


def test_a_marker_replaced_or_dropped_after_selection_is_omitted(tmp_path: Path) -> None:
    engine = _engine(tmp_path)
    for segment_id in ("sg_a", "sg_b", "sg_c"):
        _segment(engine, segment_id)
    for segment_id in ("sg_a", "sg_b", "sg_c"):
        _derive(engine, segment_id, _at(10))
    store = _store(engine)
    selected = store.markers_after(EventsPosition(_at(0)), _at(60), 10, extractor_version=None)

    _derive(engine, "sg_a", _at(20), events=3)
    _drop(engine, "sg_b", _at(20))

    assert [d.marker.segment_id for d in store.derivations(selected)] == ["sg_c"]


def test_the_derivation_read_issues_the_same_queries_at_two_sizes(tmp_path: Path) -> None:
    engine = _engine(tmp_path)
    for index in range(6):
        _segment(engine, f"sg_{index}")
        _derive(engine, f"sg_{index}", _at(index), events=index)
    store = _store(engine)
    markers = store.markers_after(EventsPosition(_at(-1)), _at(60), 10, extractor_version=None)

    small = count_queries(engine, lambda: store.derivations(markers[:2]))
    large = count_queries(engine, lambda: store.derivations(markers))

    assert small == large


def test_the_backfill_reads_select_epochs_minted_in_the_window_and_their_markers_and_drops(tmp_path: Path) -> None:
    engine = _engine(tmp_path, ("ch_1", "ch_2", "ch_3"))
    seed_lease(engine, "ch_1", epoch=1, runner_id="r1", at=_at(10))
    seed_lease(engine, "ch_1", epoch=2, runner_id="r1", at=_at(20))
    seed_lease(engine, "ch_2", epoch=1, runner_id="r1", at=_at(30))
    seed_lease(engine, "ch_3", epoch=1, runner_id="r1", at=_at(100))  # outside the window
    _segment(engine, "sg_11", "ch_1", epoch=1)
    _segment(engine, "sg_12", "ch_1", epoch=2)
    _segment(engine, "sg_21", "ch_2", epoch=1)
    _segment(engine, "sg_31", "ch_3", epoch=1)
    _derive(engine, "sg_11", _at(500))
    _derive(engine, "sg_11", _at(500), version=_V2)
    _derive(engine, "sg_21", _at(500))
    _derive(engine, "sg_31", _at(500))
    _drop(engine, "sg_12", _at(600))
    store = _store(engine)

    first = store.epochs_minted_between(_at(0), _at(50), None, 2)
    rest = store.epochs_minted_between(_at(0), _at(50), first[-1], 2)
    assert (list(first), list(rest)) == ([EpochKey("ch_1", 1), EpochKey("ch_1", 2)], [EpochKey("ch_2", 1)])
    assert store.epochs_minted_between(_at(0), _at(30), None, 10)[-1] == EpochKey("ch_1", 2)  # until is exclusive

    epochs = [*first, *rest]
    markers = store.epoch_markers(epochs, extractor_version=None)
    assert [(m.segment_id, m.extractor_version) for m in markers] == [("sg_11", _V1), ("sg_11", _V2), ("sg_21", _V1)]
    assert [m.segment_id for m in store.epoch_markers(epochs, extractor_version=_V2)] == ["sg_11"]
    assert [d.segment_id for d in store.epoch_drops(epochs)] == ["sg_12"]
    assert store.epoch_drops([EpochKey("ch_1", 1)]) == []


def test_the_events_cursor_round_trips(tmp_path: Path) -> None:
    engine = _engine(tmp_path)
    store = EgressStore(hub_store_connections(engine))
    position = EventsPosition(_at(10), "sg_a", _V1)

    store.append_cursor(EgressCheckpoint("events", None, UsagePosition(_at(0)), 3, ("a", "m"), _at(11), position))
    store.append_cursor(EgressCheckpoint("invocations", None, UsagePosition(_at(5), 7), 0, (), _at(11)))

    events = store.newest_cursor("events")
    assert events is not None
    assert (events.events, events.step, events.row_count, events.files) == (position, None, 3, ("a", "m"))
    invocations = store.newest_cursor("invocations")
    assert invocations is not None
    assert (invocations.events, invocations.step) == (None, None)
    store.append_cursor(
        EgressCheckpoint("events", None, UsagePosition(_at(0)), 0, (), _at(12), EventsPosition(_at(1)))
    )
    reset = store.newest_cursor("events")
    assert reset is not None
    assert reset.events == EventsPosition(_at(1), "", "")

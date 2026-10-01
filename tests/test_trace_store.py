"""The trace export's store reads (component tier) — facts written through the hub's real routes,
hydrated to ``StepFacts`` and selected through the window the sweep and replay share."""

from __future__ import annotations

from datetime import timedelta
from pathlib import Path

import pytest

from blizzard.foundation.event_log import EventLogKind
from blizzard.hub.domain.tracing.cursor import CursorKey
from blizzard.hub.domain.tracing.facts import (
    ChunkStoppedRecord,
    EpochOwnerRecord,
    LeaseRecord,
    RouteReleasedRecord,
    StepFacts,
    TransitionRecord,
)
from blizzard.hub.domain.tracing.repository import TraceCursorRecord
from blizzard.hub.domain.tracing.steps import StepOutcome, identify_steps
from blizzard.hub.domain.tracing.window import read_window
from blizzard.hub.store.internal.trace_store import TraceStore
from tests.support import HubHarness, count_queries, hub_store_connections
from tests.trace_hub import label, stop, trace_hub, transitioned_and_stopped

pytestmark = pytest.mark.component


def _store(hub: HubHarness) -> TraceStore:
    return TraceStore(hub_store_connections(hub.engine), graphs=hub.services.graphs, label=label)


def test_hydrated_facts_identify_the_same_steps_as_a_hand_built_fixture(tmp_path: Path) -> None:
    hub, graph = trace_hub(tmp_path)
    moved, stopped = transitioned_and_stopped(hub, graph, 1)
    at = hub.clock.now()
    claimed = at - timedelta(seconds=5)
    build = next(n for n in graph.nodes if n.name == "build")
    review = next(n for n in graph.nodes if n.name == "review")

    hydrated = _store(hub).step_facts_for([moved, stopped, "ch_missing"])

    assert set(hydrated) == {moved, stopped}
    graphs = {graph.graph_id: graph}
    expected_moved = StepFacts(
        chunk_id=moved,
        graphs=graphs,
        pin_graph_id=graph.graph_id,
        lease_facts=(LeaseRecord(1, claimed),),
        epoch_owners=(EpochOwnerRecord(1, "r1", claimed),),
        transitions=(
            TransitionRecord(
                epoch=1,
                recorded_at=at,
                graph_id=graph.graph_id,
                to_node_id=review.node_id,
                from_node_id=build.node_id,
                choice_name="pass",
            ),
        ),
    )
    expected_stopped = StepFacts(
        chunk_id=stopped,
        graphs=graphs,
        pin_graph_id=graph.graph_id,
        lease_facts=(LeaseRecord(1, claimed),),
        epoch_owners=(EpochOwnerRecord(1, "r1", claimed),),
        route_released=(RouteReleasedRecord(at),),
        chunk_stopped=(ChunkStoppedRecord(at),),
    )
    assert identify_steps(hydrated[moved]) == identify_steps(expected_moved)
    assert identify_steps(hydrated[stopped]) == identify_steps(expected_stopped)
    assert [s.close.outcome for s in identify_steps(hydrated[stopped]) if s.close] == [StepOutcome.RELEASED]
    assert hydrated[moved].work_refs == ("default#1",)
    assert set(hydrated[moved].graphs) == {graph.graph_id}


def _drain(store: TraceStore, since: CursorKey, until, limit: int) -> list[CursorKey]:  # type: ignore[no-untyped-def]
    """Every key the window tells, pass after pass, until its position stops moving."""
    told: list[CursorKey] = []
    for _ in range(50):
        window = read_window(store, since, until, limit)
        told += [c.key for c in window.steps]
        if window.position == since:
            return told
        since = window.position
    raise AssertionError("the window never settled")


@pytest.mark.parametrize("limit", [1, 2, 50])
def test_ties_across_tables_and_ambient_closers_are_told_exactly_once(tmp_path: Path, limit: int) -> None:
    hub, graph = trace_hub(tmp_path)
    moved, stopped = transitioned_and_stopped(hub, graph, 1)
    tie = hub.clock.now()
    hub.clock.advance(timedelta(seconds=10))
    stop(hub, moved)

    told = _drain(_store(hub), CursorKey.opening(tie - timedelta(seconds=1)), tie + timedelta(hours=1), limit)

    # The later stop closes nothing new: the moved chunk's only step closed at the tie.
    assert told == sorted([CursorKey(tie, moved, 1), CursorKey(tie, stopped, 1)])


def test_a_window_reading_from_a_tie_tells_only_what_sorts_after_the_cursor(tmp_path: Path) -> None:
    hub, graph = trace_hub(tmp_path)
    moved, stopped = transitioned_and_stopped(hub, graph, 1)
    first, second = sorted([CursorKey(hub.clock.now(), moved, 1), CursorKey(hub.clock.now(), stopped, 1)])

    window = read_window(_store(hub), first, hub.clock.now() + timedelta(hours=1), 50)

    assert [c.key for c in window.steps] == [second]


def test_until_holds_back_steps_closing_after_it(tmp_path: Path) -> None:
    hub, graph = trace_hub(tmp_path)
    transitioned_and_stopped(hub, graph, 1)
    now = hub.clock.now()

    window = read_window(
        _store(hub), CursorKey.opening(now - timedelta(seconds=1)), now - timedelta(microseconds=1), 50
    )

    assert window.steps == ()


def _fleet(tmp_path: Path, pairs: int) -> HubHarness:
    tmp_path.mkdir()
    hub, graph = trace_hub(tmp_path)
    for i in range(pairs):
        transitioned_and_stopped(hub, graph, 10 * (i + 1))
    return hub


def test_statement_count_is_flat_as_the_window_grows(tmp_path: Path) -> None:
    small = _fleet(tmp_path / "small", 1)
    large = _fleet(tmp_path / "large", 4)
    counts: dict[str, int] = {}
    sizes: dict[str, int] = {}
    for name, hub in (("small", small), ("large", large)):
        store = _store(hub)
        since = CursorKey.opening(hub.clock.now() - timedelta(hours=1))

        def call(store: TraceStore = store, since: CursorKey = since, name: str = name) -> None:
            sizes[name] = len(read_window(store, since, since.at + timedelta(hours=2), 50).steps)

        counts[name] = count_queries(hub.engine, call)
    assert sizes == {"small": 2, "large": 8}
    assert counts["small"] == counts["large"]


def test_cursor_rows_append_and_the_newest_is_the_position(tmp_path: Path) -> None:
    hub, _graph = trace_hub(tmp_path)
    store = _store(hub)
    now = hub.clock.now()
    assert store.newest_cursor() is None

    start = TraceCursorRecord(CursorKey.opening(now), 0, now)
    store.append_cursor(start)
    advanced = TraceCursorRecord(CursorKey(now, "ch_1", 2, "dec_1"), 7, now + timedelta(seconds=5))
    store.append_cursor(advanced)

    assert store.newest_cursor() == advanced


def test_the_export_latch_reads_the_newer_of_the_two_kinds(tmp_path: Path) -> None:
    hub, _graph = trace_hub(tmp_path)
    store = _store(hub)
    assert store.newest_export_latch() is None

    def record(kind: EventLogKind, seconds: int) -> None:
        hub.services.event_log.record(
            kind=kind,
            runner_id=None,
            chunk_id=None,
            lease_id=None,
            node_name=None,
            message=kind,
            detail=None,
            at=hub.clock.now() + timedelta(seconds=seconds),
        )

    record("trace-export-failed", 1)
    assert store.newest_export_latch() == "trace-export-failed"
    record("trace-config-rejected", 2)
    record("trace-export-recovered", 3)
    assert store.newest_export_latch() == "trace-export-recovered"

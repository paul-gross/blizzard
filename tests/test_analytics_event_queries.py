"""The analytics event query seam: every filter alone and combined, keyset paging
covering a result set exactly once with no repeats, and the four canned counts honoring
the same filters (blizzard#255, Phase 2 — component tier)."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import insert

from blizzard.foundation.store.engine import create_engine_from_url
from blizzard.hub.config import HubConfig
from blizzard.hub.domain.analytics.events import TranscriptEvent
from blizzard.hub.domain.analytics.queries import EventQueryCriteria
from blizzard.hub.runtime import migration_runner
from blizzard.hub.store import schema as s
from blizzard.hub.store.internal import transcript_event_store as event_store_module
from blizzard.hub.store.internal.analytics_event_query_store import AnalyticsEventQueryStore

pytestmark = pytest.mark.component

_VERSION = "blizzard-analytics/2"
_OTHER_VERSION = "blizzard-analytics/1"
_NOW = datetime(2026, 8, 12, 12, 0, tzinfo=UTC)


def _event(**overrides: object) -> TranscriptEvent:
    values: dict[str, object] = {
        "kind": "file_read",
        "turn_path": "0",
        "occurrence": 0,
        "payload": "{}",
        "subject": "src/a.py",
        "tool": "Read",
        "chunk_id": "ch_1",
        "node_id": "nd_build",
        "epoch": 1,
        "spawn_generation": 1,
        "graph_id": "gr_1",
        "depth": 0,
        "agent_type": None,
        "occurred_at": _NOW,
    }
    values.update(overrides)
    return TranscriptEvent(**values)  # type: ignore[arg-type]


def _criteria(**overrides: object) -> EventQueryCriteria:
    values: dict[str, object] = {"extractor_version": _VERSION}
    values.update(overrides)
    return EventQueryCriteria(**values)  # type: ignore[arg-type]


def _new_store(tmp_path: Path) -> tuple[AnalyticsEventQueryStore, Any]:
    """A migrated, empty store plus the writer its tests seed through — the write path is
    :mod:`transcript_event_store`'s own statement, never ad-hoc SQL."""
    db_url = f"sqlite:///{tmp_path / 'hub.db'}"
    migration_runner(HubConfig(root=tmp_path, db_url=db_url)).upgrade("head")
    engine = create_engine_from_url(db_url)

    def insert_events(segment_id: str, *events: TranscriptEvent, extractor_version: str = _VERSION) -> None:
        with engine.begin() as conn:
            conn.execute(event_store_module._insert_events_stmt(segment_id, extractor_version, list(events)))

    return AnalyticsEventQueryStore(engine), insert_events


@pytest.fixture
def store(tmp_path: Path) -> AnalyticsEventQueryStore:
    store, insert_events = _new_store(tmp_path)
    engine = store._engine
    insert_events(
        "sg_1",
        _event(kind="file_read", subject="src/a.py", tool="Read", node_id="nd_build", graph_id="gr_1"),
        _event(
            kind="file_read",
            turn_path="1",
            subject="src/b.py",
            tool="Read",
            node_id="nd_build",
            graph_id="gr_1",
            chunk_id="ch_1",
            occurred_at=_NOW.replace(hour=13),
        ),
        _event(
            kind="skill_invocation",
            subject="wf-commit",
            tool="Skill",
            payload="{}",
            node_id="nd_build",
            graph_id="gr_1",
            chunk_id="ch_1",
        ),
        _event(
            kind="agent_spawn",
            subject="explorer",
            tool="Task",
            agent_type=None,
            node_id="nd_build",
            graph_id="gr_1",
            chunk_id="ch_1",
        ),
    )
    insert_events(
        "sg_2",
        _event(
            kind="file_read",
            subject="src/c.py",
            tool="Read",
            node_id="nd_review",
            graph_id="gr_2",
            chunk_id="ch_2",
            agent_type="explorer",
            occurred_at=_NOW.replace(day=13),
        ),
    )
    # A prior-version row — must never surface under `_VERSION`'s reads (D1: mixing
    # versions double-counts the same occurrence).
    insert_events(
        "sg_1",
        _event(kind="file_read", subject="src/a.py", tool="Read", node_id="nd_build", graph_id="gr_1"),
        extractor_version=_OTHER_VERSION,
    )
    with engine.begin() as conn:
        conn.execute(insert(s.chunk_work_refs).values(chunk_id="ch_1", source="github", ref="255"))
        conn.execute(insert(s.chunk_work_refs).values(chunk_id="ch_2", source="jira", ref="EX-1"))

    return store


# --- events: each filter alone -------------------------------------------------


def test_extractor_version_scopes_reads_to_one_version(store: AnalyticsEventQueryStore) -> None:
    page = store.events(_criteria())
    assert len(page.events) == 5
    assert all(e.tool != "" for e in page.events)

    page_other = store.events(_criteria(extractor_version=_OTHER_VERSION))
    assert len(page_other.events) == 1


def test_kind_filters_events(store: AnalyticsEventQueryStore) -> None:
    page = store.events(_criteria(kind="skill_invocation"))
    assert [e.subject for e in page.events] == ["wf-commit"]


def test_tool_filters_events(store: AnalyticsEventQueryStore) -> None:
    page = store.events(_criteria(tool="Task"))
    assert [e.subject for e in page.events] == ["explorer"]


def test_path_prefix_filters_events_by_subject(store: AnalyticsEventQueryStore) -> None:
    page = store.events(_criteria(path_prefix="src/"))
    assert {e.subject for e in page.events} == {"src/a.py", "src/b.py", "src/c.py"}


def test_node_id_filters_events(store: AnalyticsEventQueryStore) -> None:
    page = store.events(_criteria(node_id="nd_review"))
    assert [e.subject for e in page.events] == ["src/c.py"]


def test_graph_id_filters_events(store: AnalyticsEventQueryStore) -> None:
    page = store.events(_criteria(graph_id="gr_2"))
    assert [e.subject for e in page.events] == ["src/c.py"]


def test_source_filters_by_chunk_work_ref_existence(store: AnalyticsEventQueryStore) -> None:
    page = store.events(_criteria(source="jira"))
    assert {e.chunk_id for e in page.events} == {"ch_2"}


def test_time_range_filters_events(store: AnalyticsEventQueryStore) -> None:
    page = store.events(_criteria(since=_NOW.replace(hour=12, minute=30)))
    assert {e.subject for e in page.events} == {"src/b.py", "src/c.py"}

    page = store.events(_criteria(until=_NOW.replace(hour=12, minute=30)))
    assert {e.subject for e in page.events} == {"src/a.py", "wf-commit", "explorer"}


def test_filters_combine(store: AnalyticsEventQueryStore) -> None:
    page = store.events(_criteria(kind="file_read", node_id="nd_build", path_prefix="src/b"))
    assert [e.subject for e in page.events] == ["src/b.py"]


def test_path_prefix_treats_like_wildcards_as_literal_characters(tmp_path: Path) -> None:
    """A prefix is a prefix, not a pattern: real paths carry ``_`` constantly, so an
    unescaped LIKE would quietly return files the caller never asked for."""
    store, insert_events = _new_store(tmp_path)
    insert_events(
        "sg_1",
        _event(subject="src/a_b.py"),
        _event(turn_path="1", subject="src/aXb.py"),
        _event(turn_path="2", subject="src/a%c.py"),
        _event(turn_path="3", subject="src/azc.py"),
    )

    assert [e.subject for e in store.events(_criteria(path_prefix="src/a_b")).events] == ["src/a_b.py"]
    assert [e.subject for e in store.events(_criteria(path_prefix="src/a%c")).events] == ["src/a%c.py"]
    assert [r.key for r in store.counts_by_file(_criteria(path_prefix="src/a_b"))] == ["src/a_b.py"]


# --- events: keyset paging ------------------------------------------------------


def test_paging_covers_the_result_set_exactly_once_with_no_repeats(store: AnalyticsEventQueryStore) -> None:
    seen: list[int] = []
    cursor: str | None = None
    for _ in range(10):  # generous bound on iterations for a 5-row set
        page = store.events(_criteria(), cursor=cursor, limit=1)
        assert len(page.events) == 1
        seen.append(page.events[0].id)
        cursor = page.next_cursor
        if cursor is None:
            break
    assert cursor is None
    assert sorted(seen) == seen  # ordered
    assert len(seen) == len(set(seen)) == 5


# --- counts: honor the same filters ---------------------------------------------


def test_counts_by_file(store: AnalyticsEventQueryStore) -> None:
    rows = store.counts_by_file(_criteria())
    assert {(r.key, r.count) for r in rows} == {("src/a.py", 1), ("src/b.py", 1), ("src/c.py", 1)}


def test_counts_by_file_honors_a_combined_filter(store: AnalyticsEventQueryStore) -> None:
    rows = store.counts_by_file(_criteria(node_id="nd_build"))
    assert {(r.key, r.count) for r in rows} == {("src/a.py", 1), ("src/b.py", 1)}


def test_counts_by_skill(store: AnalyticsEventQueryStore) -> None:
    rows = store.counts_by_skill(_criteria())
    assert {(r.key, r.count) for r in rows} == {("wf-commit", 1)}


def test_counts_by_agent_type(store: AnalyticsEventQueryStore) -> None:
    rows = store.counts_by_agent_type(_criteria())
    assert {(r.key, r.count) for r in rows} == {("explorer", 1)}


def test_counts_by_node(store: AnalyticsEventQueryStore) -> None:
    rows = store.counts_by_node(_criteria())
    assert {(r.key, r.count) for r in rows} == {("nd_build", 4), ("nd_review", 1)}


def test_counts_are_ordered_most_frequent_first(store: AnalyticsEventQueryStore) -> None:
    assert [r.key for r in store.counts_by_node(_criteria())] == ["nd_build", "nd_review"]


def test_a_canned_counts_own_kind_intersects_the_criteria_kind_rather_than_replacing_it(
    store: AnalyticsEventQueryStore,
) -> None:
    """A caller naming a kind this count cannot serve asked for an empty scope — it must
    not get the count's own kind back under the name it did not ask for."""
    assert store.counts_by_file(_criteria(kind="skill_invocation")) == []
    assert [r.key for r in store.counts_by_file(_criteria(kind="file_read"))] == ["src/a.py", "src/b.py", "src/c.py"]

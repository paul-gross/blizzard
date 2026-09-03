"""``ChunkDependenciesStore`` — the declared dependency edge's round-trip and release
(issue #456, component tier). Migrated-to-head sqlite-on-disk, the
``test_chunk_delivery_store.py`` shape."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy import Engine, func, select

from blizzard.foundation.clock import FixedClock
from blizzard.hub.domain.chunks.dependencies import IWriteChunkDependenciesRepository
from blizzard.hub.store import schema as s
from tests.support import chunk_stores, migrate_to, seed_chunk, seed_graph

pytestmark = pytest.mark.component

_NOW = datetime(2026, 9, 2, 12, 0, 0, tzinfo=UTC)


def _dependencies(tmp_path: Path) -> tuple[IWriteChunkDependenciesRepository, Engine]:
    _, engine = migrate_to(tmp_path, "head")
    with engine.begin() as conn:
        seed_graph(conn, "gr_1", at=_NOW)
        seed_chunk(conn, "ch_dependent", graph_id="gr_1", at=_NOW)
        seed_chunk(conn, "ch_prereq", graph_id="gr_1", at=_NOW)
    return chunk_stores(engine, FixedClock(instant=_NOW)).dependencies, engine


def test_declared_edge_round_trips_through_the_seam(tmp_path: Path) -> None:
    dependencies, _ = _dependencies(tmp_path)

    declared = dependencies.declare("ch_dependent", "ch_prereq", by="user:alice", at=_NOW)

    assert declared.dependent_chunk_id == "ch_dependent"
    assert declared.prerequisite_chunk_id == "ch_prereq"
    assert declared.declared_by == "user:alice"
    assert declared.declared_at == _NOW
    assert declared.released_at is None
    assert declared.released_by is None
    assert declared.standing is True

    fetched = dependencies.standing_edge("ch_dependent", "ch_prereq")
    assert fetched == declared
    assert dependencies.list_standing_edges() == [declared]


def test_released_edge_reads_released_and_still_exists(tmp_path: Path) -> None:
    dependencies, engine = _dependencies(tmp_path)
    declared = dependencies.declare("ch_dependent", "ch_prereq", by="user:alice", at=_NOW)
    released_at = _NOW + timedelta(hours=1)

    released = dependencies.release("ch_dependent", "ch_prereq", by="user:bob", at=released_at)

    assert released is not None
    assert released.dependency_id == declared.dependency_id
    assert released.released_at == released_at
    assert released.released_by == "user:bob"
    assert released.standing is False

    # No longer standing — absent from both standing reads.
    assert dependencies.standing_edge("ch_dependent", "ch_prereq") is None
    assert dependencies.list_standing_edges() == []

    # The row is never deleted — it still exists, released.
    with engine.connect() as conn:
        count = conn.execute(
            select(func.count())
            .select_from(s.chunk_dependencies)
            .where(s.chunk_dependencies.c.dependency_id == declared.dependency_id)
        ).scalar()
    assert count == 1


def test_release_is_a_no_op_when_no_edge_stands(tmp_path: Path) -> None:
    dependencies, _ = _dependencies(tmp_path)

    assert dependencies.release("ch_dependent", "ch_prereq", by="user:bob", at=_NOW) is None


def test_declare_after_release_mints_a_fresh_row(tmp_path: Path) -> None:
    dependencies, _ = _dependencies(tmp_path)
    first = dependencies.declare("ch_dependent", "ch_prereq", by="user:alice", at=_NOW)
    dependencies.release("ch_dependent", "ch_prereq", by="user:alice", at=_NOW + timedelta(hours=1))

    second = dependencies.declare("ch_dependent", "ch_prereq", by="user:alice", at=_NOW + timedelta(hours=2))

    assert second.dependency_id != first.dependency_id
    assert dependencies.standing_edge("ch_dependent", "ch_prereq") == second


def test_list_standing_edges_orders_by_declared_at_ascending(tmp_path: Path) -> None:
    """Review round 1 F2: ``derive_blocked_markings``'s "earliest-declared wins" rule
    (D4) delegates entirely to this ordering, so it must be pinned here — declared with
    genuinely different ``declared_at`` instants, and out of chronological call order, so
    a store that returned insertion order rather than sorting would fail this."""
    dependencies, engine = _dependencies(tmp_path)
    with engine.begin() as conn:
        seed_chunk(conn, "ch_prereq_2", graph_id="gr_1", at=_NOW)

    later = dependencies.declare("ch_dependent", "ch_prereq_2", by="user:alice", at=_NOW + timedelta(hours=1))
    earlier = dependencies.declare("ch_dependent", "ch_prereq", by="user:alice", at=_NOW)

    ordered = dependencies.list_standing_edges()

    assert [e.dependency_id for e in ordered] == [earlier.dependency_id, later.dependency_id]


def test_list_standing_edges_breaks_a_declared_at_tie_by_dependency_id(tmp_path: Path) -> None:
    """The same rule's tiebreak (``bzh:sql-portable`` — an explicit total order, never an
    implicit one): two edges declared at the identical instant still resolve to one
    deterministic order, ascending by the minted ``dependency_id``."""
    dependencies, engine = _dependencies(tmp_path)
    with engine.begin() as conn:
        seed_chunk(conn, "ch_prereq_2", graph_id="gr_1", at=_NOW)

    first = dependencies.declare("ch_dependent", "ch_prereq", by="user:alice", at=_NOW)
    second = dependencies.declare("ch_dependent", "ch_prereq_2", by="user:alice", at=_NOW)

    ordered = dependencies.list_standing_edges()

    assert [e.dependency_id for e in ordered] == sorted([first.dependency_id, second.dependency_id])

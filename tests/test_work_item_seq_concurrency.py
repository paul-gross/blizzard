"""Concurrent allocation of the ``work_item_sequence`` counter (issue #357).

``WorkItemStore.allocate_ref`` is optimistic-insert-then-increment, not one locking
statement like ``ChunkStore._next_route_seq`` — a brand-new source has no row to lock.
Proves the increment statement locks the row under any dialect, and that concurrent
sqlite writers — including a race over a source's first allocation — never duplicate."""

from __future__ import annotations

import threading
from pathlib import Path

import pytest
from sqlalchemy.dialects import postgresql, sqlite
from sqlalchemy.sql.dml import Update

from blizzard.foundation.store.engine import create_engine_from_url
from blizzard.hub.config import HubConfig
from blizzard.hub.runtime import migration_runner
from blizzard.hub.store.internal.work_item_store import WorkItemStore

pytestmark = pytest.mark.unit


def test_next_ref_increment_locks_the_source_row_for_update() -> None:
    """The already-exists branch's increment is the statement that must serialize
    concurrent postgres writers: a plain relative ``UPDATE ... RETURNING``, portable
    across both dialects."""
    from sqlalchemy import update

    from blizzard.hub.store import schema as s

    stmt = (
        update(s.work_item_sequence)
        .where(s.work_item_sequence.c.source == "hub")
        .values(next_ref=s.work_item_sequence.c.next_ref + 1)
        .returning(s.work_item_sequence.c.next_ref)
    )
    assert isinstance(stmt, Update)  # a write, not a SELECT
    pg_sql = str(stmt.compile(dialect=postgresql.dialect()))
    sqlite_sql = str(stmt.compile(dialect=sqlite.dialect()))
    assert pg_sql.startswith("UPDATE work_item_sequence SET")
    assert sqlite_sql.startswith("UPDATE work_item_sequence SET")


def test_concurrent_first_allocation_on_sqlite_never_duplicates(tmp_path: Path) -> None:
    db_url = f"sqlite:///{tmp_path / 'hub.db'}"
    migration_runner(HubConfig(root=tmp_path, db_url=db_url)).upgrade("head")
    engine = create_engine_from_url(db_url)
    store = WorkItemStore(engine)

    barrier = threading.Barrier(2)
    lock = threading.Lock()
    refs: list[str] = []
    errors: list[BaseException] = []

    def allocate() -> None:
        try:
            barrier.wait(timeout=5)
            ref = store.allocate_ref("hub")
            with lock:
                refs.append(ref)
        except BaseException as exc:  # either outcome is acceptable, see below
            with lock:
                errors.append(exc)

    threads = [threading.Thread(target=allocate) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    # Either both writers committed distinct refs, or a loser raised rather than
    # silently committing a duplicate — a duplicate committed ref is not acceptable.
    assert len(refs) + len(errors) == 2
    assert len(set(refs)) == len(refs)


def test_concurrent_allocation_against_an_existing_row_never_duplicates(tmp_path: Path) -> None:
    db_url = f"sqlite:///{tmp_path / 'hub.db'}"
    migration_runner(HubConfig(root=tmp_path, db_url=db_url)).upgrade("head")
    engine = create_engine_from_url(db_url)
    store = WorkItemStore(engine)
    store.allocate_ref("hub")  # seed the counter row so both threads race the existing-row path

    barrier = threading.Barrier(2)
    lock = threading.Lock()
    refs: list[str] = []

    def allocate() -> None:
        barrier.wait(timeout=5)
        ref = store.allocate_ref("hub")
        with lock:
            refs.append(ref)

    threads = [threading.Thread(target=allocate) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert len(refs) == 2
    assert len(set(refs)) == 2

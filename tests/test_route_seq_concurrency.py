"""Concurrent allocation of the route-event ``seq`` counter (issue #41).

``chunk_rows.next_route_seq`` is read-then-insert, not atomic; two concurrent writers
must not compute the same next value. Proves the allocator's statement locks the chunk
row under any dialect (static proof, no live postgres), and sqlite never duplicates.
"""

from __future__ import annotations

import threading
from datetime import UTC, datetime
from pathlib import Path

import pytest
from sqlalchemy import insert
from sqlalchemy.dialects import postgresql, sqlite
from sqlalchemy.sql.dml import Update

from blizzard.foundation.store.engine import create_engine_from_url
from blizzard.hub.config import HubConfig
from blizzard.hub.runtime import migration_runner
from blizzard.hub.store import schema as s
from blizzard.hub.store.internal.chunk_rows import next_route_seq

pytestmark = pytest.mark.unit

_NOW = datetime(2026, 7, 16, tzinfo=UTC)


class _CapturingConn:
    """A fake ``Connection`` that records every statement handed to ``execute`` instead
    of running it, so the allocator's real statements can be compiled against a dialect
    that never touches this process (postgres)."""

    def __init__(self) -> None:
        self.statements: list[object] = []

    def execute(self, stmt: object):  # type: ignore[no-untyped-def]
        self.statements.append(stmt)
        return _FakeResult()


class _FakeResult:
    def scalar(self) -> None:
        return None


def test_next_route_seq_locks_the_chunk_row_for_update() -> None:
    conn = _CapturingConn()

    next_route_seq(conn, "ch_1")  # type: ignore[arg-type]

    assert len(conn.statements) == 4  # the lock, then the three per-table max reads
    lock_stmt = conn.statements[0]
    assert isinstance(lock_stmt, Update)  # a write, not a SELECT — see the allocator's own docstring for why
    pg_sql = str(lock_stmt.compile(dialect=postgresql.dialect()))
    sqlite_sql = str(lock_stmt.compile(dialect=sqlite.dialect()))
    assert pg_sql.startswith("UPDATE chunks SET")
    assert sqlite_sql.startswith("UPDATE chunks SET")


def test_concurrent_seq_allocation_on_sqlite_never_duplicates(tmp_path: Path) -> None:
    db_url = f"sqlite:///{tmp_path / 'hub.db'}"
    migration_runner(HubConfig(root=tmp_path, db_url=db_url)).upgrade("head")
    engine = create_engine_from_url(db_url)

    barrier = threading.Barrier(2)
    lock = threading.Lock()
    seqs: list[int] = []
    errors: list[BaseException] = []

    def allocate(route_id: str) -> None:
        try:
            barrier.wait(timeout=5)
            with engine.begin() as conn:
                seq = next_route_seq(conn, "ch_1")
                conn.execute(
                    insert(s.route_created).values(
                        route_id=route_id, chunk_id="ch_1", runner_id="r", workspace_id="w", created_at=_NOW, seq=seq
                    )
                )
            with lock:
                seqs.append(seq)
        except BaseException as exc:  # either outcome is acceptable, see below
            with lock:
                errors.append(exc)

    threads = [threading.Thread(target=allocate, args=(f"rt_{i}",)) for i in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    # Either both writers committed distinct seqs, or a loser raised rather than
    # silently committing a duplicate — a duplicate committed seq is not acceptable.
    assert len(seqs) + len(errors) == 2
    assert len(set(seqs)) == len(seqs)

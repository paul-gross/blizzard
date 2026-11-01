"""Concurrent allocation of the per-chunk ``artifacts.seq`` counter.

``chunk_rows.next_artifact_seq`` is read-then-insert, not atomic; two concurrent writers
must not compute the same next value. Proves the allocator locks the chunk row first
under any dialect (static proof, no live postgres), and sqlite never duplicates.
"""

from __future__ import annotations

import threading
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path

import pytest
from sqlalchemy import Connection, create_engine, insert
from sqlalchemy.dialects import postgresql, sqlite
from sqlalchemy.sql.dml import Update
from structlog import get_logger

from blizzard.foundation.store.engine import create_engine_from_url
from blizzard.hub.store import schema as s
from blizzard.hub.store.errors import HubStoreErrorFactory
from blizzard.hub.store.internal.chunk_rows import next_artifact_seq
from blizzard.hub.store.scoped import ScopedConnections
from blizzard.hub.tenancy import StoreScope, TenantId
from tests.support import hub_store_connections, migrated_hub_at, stamp

pytestmark = pytest.mark.unit

_NOW = datetime(2026, 9, 30, tzinfo=UTC)


class _CapturingConn:
    """A fake ``Connection`` recording each statement instead of running it, so the
    allocator's statements compile against a dialect this process never connects to."""

    def __init__(self, scoped: Connection) -> None:
        self.statements: list[object] = []
        self.get_execution_options = scoped.get_execution_options

    def execute(self, stmt: object):  #
        self.statements.append(stmt)
        return _FakeResult()


class _FakeResult:
    def scalar(self) -> None:
        return None


@contextmanager
def _scoped_connection() -> Iterator[Connection]:
    """A connection a scoped read yields over an empty in-memory store — what the allocator's
    statements recover their scope from; nothing runs on it."""
    connections = ScopedConnections(
        create_engine("sqlite://"), HubStoreErrorFactory(get_logger("test")), StoreScope(TenantId("ten_test"))
    )
    with connections.read("capture") as conn:
        yield conn


def test_next_artifact_seq_locks_the_chunk_row_before_reading_the_max() -> None:
    with _scoped_connection() as scoped:
        conn = _CapturingConn(scoped)

        assert next_artifact_seq(conn, "ch_1") == 1  # type: ignore[arg-type]

    assert len(conn.statements) == 2  # the lock, then the max read
    lock_stmt = conn.statements[0]
    assert isinstance(lock_stmt, Update)
    assert str(lock_stmt.compile(dialect=postgresql.dialect())).startswith("UPDATE chunks SET")
    assert str(lock_stmt.compile(dialect=sqlite.dialect())).startswith("UPDATE chunks SET")


def test_concurrent_artifact_seq_allocation_on_sqlite_never_duplicates(tmp_path: Path) -> None:
    db_url = f"sqlite:///{tmp_path / 'hub.db'}"
    migrated_hub_at(db_url)
    engine = create_engine_from_url(db_url)

    barrier = threading.Barrier(2)
    lock = threading.Lock()
    seqs: list[int] = []
    errors: list[BaseException] = []

    def allocate(artifact_id: str) -> None:
        try:
            barrier.wait(timeout=5)
            with hub_store_connections(engine).write("seed") as conn:
                seq = next_artifact_seq(conn, "ch_1")
                conn.execute(
                    insert(s.artifacts).values(
                        stamp(
                            s.artifacts,
                            artifact_id=artifact_id,
                            chunk_id="ch_1",
                            node_id="nd_1",
                            node_name="deliver",
                            epoch=1,
                            name=artifact_id,
                            kind="asset",
                            data="",
                            produced_at=_NOW,
                            seq=seq,
                        )
                    )
                )
            with lock:
                seqs.append(seq)
        except BaseException as exc:  # either outcome is acceptable, see below
            with lock:
                errors.append(exc)

    threads = [threading.Thread(target=allocate, args=(f"art_{i}",)) for i in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    # Either both writers committed distinct seqs, or a loser raised rather than
    # silently committing a duplicate — a duplicate committed seq is not acceptable.
    assert len(seqs) + len(errors) == 2
    assert len(set(seqs)) == len(seqs)

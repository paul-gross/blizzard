"""Component coverage for the invariant checker's one read (``bzh:invariant-checker``): each half reads inside one
read transaction, so a write committed mid-run is not seen, and the hub half's borrowed connections only read, under
the tenant statement guard."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest
import sqlalchemy as sa
from sqlalchemy import Connection, Engine, event

from blizzard.foundation.store.engine import create_engine_from_url
from blizzard.foundation.store.read_transaction import read_transaction
from blizzard.hub.store import schema as hub
from blizzard.hub.store.errors import BorrowedWrite
from blizzard.hub.store.internal.invariants import _hub_wide
from blizzard.hub.store.scoped import UnscopedConnection, statements_of
from blizzard.hub.store.tenant_guard import UnscopedTenantStatement
from blizzard.hub.tenancy import StoreScope
from blizzard.runner.store import schema as runner
from blizzard.tools.invariants import hub_invariants, runner_invariants
from tests.config_inputs import init_host, init_hub
from tests.migration_support_invariant_checker import NOW, insert
from tests.runner_fakes import FIXTURE_RUNNER_ID, FakeProbe
from tests.support import default_tenant, hub_level_connections, hub_scoped, stamp

pytestmark = [pytest.mark.component]


def _land_once_after(
    engine: Engine, landed: Callable[[Connection, str], bool], write: Callable[[], None]
) -> list[bool]:
    """Commit ``write`` from a second connection right after the first statement ``landed`` accepts has executed;
    the returned list holds one entry once it has."""
    fired: list[bool] = []

    def after(conn: Connection, cursor: Any, statement: str, *args: Any) -> None:
        if not fired and landed(conn, statement):
            fired.append(True)
            write()

    event.listen(engine, "after_cursor_execute", after)
    return fired


def test_the_runner_half_does_not_see_a_seq_committed_after_it_read_the_counters(tmp_path: Path) -> None:
    engine = create_engine_from_url(init_host(tmp_path / "runner").db_url)
    with engine.begin() as conn:
        conn.execute(
            sa.insert(runner.runner_identity).values(runner_id=FIXTURE_RUNNER_ID, runner_name="r", registered_at=NOW)
        )
    writer = create_engine_from_url(str(engine.url))

    def seq_without_counter() -> None:
        with writer.begin() as conn:
            conn.execute(
                insert(runner.outbound_buffer).values(
                    seq=1, kind="lease.minted", chunk_id="ch_1", lease_id="l", payload="{}", created_at=NOW
                )
            )

    fired = _land_once_after(engine, lambda _conn, statement: "FROM outbox_counters" in statement, seq_without_counter)

    assert runner_invariants(engine, process=FakeProbe()).run() == []
    assert fired
    # The seq did commit: a later run sees it, and no counter above it.
    assert "runner:outbox-counter-above-seq" in {v.invariant for v in runner_invariants(engine).run()}


def _scoped(conn: Connection) -> bool:
    try:
        statements_of(conn)
    except UnscopedConnection:
        return False
    return True


def test_the_hub_half_does_not_see_a_record_committed_after_it_loaded_a_tenants_facts(tmp_path: Path) -> None:
    engine = create_engine_from_url(init_hub(tmp_path / "hub").db_url)
    writer = create_engine_from_url(str(engine.url))

    def record_without_facts() -> None:
        with writer.begin() as conn:
            conn.execute(
                sa.insert(hub.chunks).values(
                    stamp(hub.chunks, chunk_id="ch_late", graph_id="gr_1", minted_at=NOW, model="m")
                )
            )

    # The tenant's facts load pins its chunks' graphs first, on a connection carrying the tenant's scope. A read that
    # saw the commit after it would list the chunk's record with no facts loaded for it.
    def pinned_graphs(conn: Connection, statement: str) -> bool:
        return _scoped(conn) and statement.startswith("SELECT chunks.chunk_id, chunks.graph_id")

    fired = _land_once_after(engine, pinned_graphs, record_without_facts)

    assert hub_invariants(engine).run() == []
    assert fired
    with writer.connect() as conn:
        assert conn.execute(hub_scoped(sa.select(hub.chunks.c.chunk_id))).scalars().all() == ["ch_late"]


def test_connections_borrowed_over_a_read_transaction_refuse_to_write(tmp_path: Path) -> None:
    engine = create_engine_from_url(init_hub(tmp_path / "hub").db_url)
    scope = StoreScope(default_tenant(engine))
    with hub_level_connections(engine).read_transaction("read") as borrowed:
        with pytest.raises(BorrowedWrite), borrowed.write("write"):
            pass
        scoped = borrowed.scoped(scope)
        with pytest.raises(BorrowedWrite), scoped.write("write"):
            pass
        with pytest.raises(BorrowedWrite), scoped.teardown("teardown"):
            pass


def test_the_guard_refuses_an_unscoped_tenant_read_through_borrowed_connections(tmp_path: Path) -> None:
    engine = create_engine_from_url(init_hub(tmp_path / "hub").db_url)
    scope = StoreScope(default_tenant(engine))
    with hub_level_connections(engine).read_transaction("read") as borrowed:
        with borrowed.scoped(scope).read("scoped") as conn:
            conn.execute(statements_of(conn).select(hub.chunks, hub.chunks.c.chunk_id))
        with (
            borrowed.scoped(scope).read("rebound") as conn,
            pytest.raises(UnscopedTenantStatement, match="chunks"),
        ):
            conn.execute(sa.select(hub.chunks.c.chunk_id))


def test_the_hub_wide_mark_is_gone_from_the_shared_connection_once_the_hub_wide_checks_end(tmp_path: Path) -> None:
    engine = create_engine_from_url(init_hub(tmp_path / "hub").db_url)
    scope = StoreScope(default_tenant(engine))
    with hub_level_connections(engine).read_transaction("read") as borrowed:
        with _hub_wide(borrowed) as conn:
            conn.execute(sa.select(hub.chunks.c.chunk_id))
        with (
            borrowed.scoped(scope).read("tenant") as conn,
            pytest.raises(UnscopedTenantStatement, match="chunks"),
        ):
            conn.execute(sa.select(hub.chunks.c.chunk_id))


def test_a_non_sqlite_engine_reads_repeatable_read_only_and_issues_no_begin() -> None:
    engine = MagicMock()
    engine.dialect.name = "postgresql"
    conn = engine.connect.return_value.__enter__.return_value
    with read_transaction(engine):
        pass
    conn.execution_options.assert_called_once_with(isolation_level="REPEATABLE READ", postgresql_readonly=True)
    conn.exec_driver_sql.assert_not_called()
    conn.rollback.assert_called_once_with()


def test_a_sqlite_engine_holds_a_begin_and_sets_no_isolation_level() -> None:
    engine = MagicMock()
    engine.dialect.name = "sqlite"
    conn = engine.connect.return_value.__enter__.return_value
    with read_transaction(engine):
        pass
    conn.exec_driver_sql.assert_called_once_with("BEGIN")
    conn.execution_options.assert_not_called()
    conn.rollback.assert_called_once_with()

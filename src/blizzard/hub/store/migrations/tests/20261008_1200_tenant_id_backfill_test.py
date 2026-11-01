"""Tests proving the ``20261008_1200_tenant_id_backfill`` revision."""

from __future__ import annotations

from pathlib import Path

import pytest
import sqlalchemy as sa
from alembic.script import ScriptDirectory

from blizzard.hub.config import HubConfig
from blizzard.hub.runtime import migration_runner
from blizzard.hub.store import MIGRATIONS_DIR
from tests.migration_support_tenant_carry_over_migration import (
    BACKFILL,
    BEFORE,
    SEED,
    T0,
    TENANTS,
    keyed_tables,
    row_counts,
    rows_each_update_changed,
    schema,
    scratch_store,
    seed_every_tenant_owned_table,
    tenant_rows,
    tenants_of,
)

pytestmark = [pytest.mark.component]


def _backfill_batch_rows() -> int:
    """The backfill's batch size, read from the revision as Alembic loads it."""
    revision = ScriptDirectory(str(MIGRATIONS_DIR)).get_revision(BACKFILL)
    assert revision is not None
    return revision.module._BATCH_ROWS


def _seed_past_a_batch(engine: sa.Engine) -> int:
    """Fill ``event_log`` (an integer key) and ``graph_artifacts`` (a two-column text key, its rows spread over
    several graphs so a page boundary falls inside one graph) with more rows than two of the backfill's batches hold,
    leaving a part-filled last page. Returns the row count seeded into each."""
    count = 2 * _backfill_batch_rows() + 7
    with engine.begin() as conn:
        conn.execute(
            sa.text("INSERT INTO event_log (recorded_at, severity, kind, message) VALUES (:at, 'info', 'k', 'm')"),
            [{"at": T0}] * count,
        )
        conn.execute(
            sa.text("INSERT INTO graph_artifacts (graph_id, name, ordinal, content) VALUES (:graph, :name, 1, '')"),
            [{"graph": f"gr_{n % 3}", "name": f"artifact-{n:05}"} for n in range(count)],
        )
    return count


def _tenant_counts(engine: sa.Engine, table: str) -> dict[str | None, int]:
    tenant = sa.column("tenant_id", sa.String)
    with engine.connect() as conn:
        rows = conn.execute(sa.select(tenant, sa.func.count()).select_from(sa.table(table)).group_by(tenant)).tuples()
        return dict(rows.all())


def test_a_rerun_of_the_backfill_over_a_partly_keyed_store_finishes(tmp_path: Path) -> None:
    """Re-entry: a run that stopped after keying some tables, and stamping some of their rows, is
    completed — no column added twice, every row still null stamped."""
    db_url, engine = scratch_store(tmp_path)
    runner = migration_runner(HubConfig(root=tmp_path, db_url=db_url))
    try:
        runner.upgrade(BEFORE)
        with engine.begin() as conn:
            for statement in SEED:
                conn.execute(sa.text(statement), {"at": T0})
        runner.upgrade(TENANTS)
        [(tenant_id, *_)] = tenant_rows(engine)
        with engine.begin() as conn:
            conn.execute(sa.text("ALTER TABLE graphs ADD COLUMN tenant_id VARCHAR"))
            conn.execute(sa.text("ALTER TABLE chunks ADD COLUMN tenant_id VARCHAR"))
            conn.execute(
                sa.text("UPDATE chunks SET tenant_id = :tenant WHERE chunk_id = 'ch_1'"), {"tenant": tenant_id}
            )

        runner.upgrade(BACKFILL)

        assert all(keyed_tables(engine).values())
        assert [c["name"] for c in sa.inspect(engine).get_columns("chunks")].count("tenant_id") == 1
        for table in ("graphs", "chunks", "event_log"):
            assert set(tenants_of(engine, table)) == {tenant_id}, table
    finally:
        engine.dispose()


def test_the_backfill_stamps_every_row_of_a_table_holding_more_than_one_batch(tmp_path: Path) -> None:
    db_url, engine = scratch_store(tmp_path)
    runner = migration_runner(HubConfig(root=tmp_path, db_url=db_url))
    try:
        runner.upgrade(TENANTS)
        count = _seed_past_a_batch(engine)
        [(tenant_id, *_)] = tenant_rows(engine)

        with rows_each_update_changed() as updates:
            runner.upgrade(BACKFILL)

        for table in ("event_log", "graph_artifacts"):
            assert _tenant_counts(engine, table) == {tenant_id: count}, table
            stamped = [rows for updated, rows in updates if updated == table]
            assert len(stamped) >= 3, table
            assert max(stamped) <= _backfill_batch_rows(), table
            assert sum(stamped) == count, table
    finally:
        engine.dispose()


def test_a_rerun_of_the_backfill_over_rows_stamped_across_batches_stamps_the_rest(tmp_path: Path) -> None:
    """Re-entry inside a table: the rows a stopped run already stamped are scattered through the key range and span
    whole pages, and the rerun stamps every row between and after them."""
    db_url, engine = scratch_store(tmp_path)
    runner = migration_runner(HubConfig(root=tmp_path, db_url=db_url))
    try:
        runner.upgrade(TENANTS)
        count = _seed_past_a_batch(engine)
        batch = _backfill_batch_rows()
        [(tenant_id, *_)] = tenant_rows(engine)
        with engine.begin() as conn:
            conn.execute(sa.text("ALTER TABLE event_log ADD COLUMN tenant_id VARCHAR"))
            conn.execute(sa.text("ALTER TABLE graph_artifacts ADD COLUMN tenant_id VARCHAR"))
            conn.execute(
                sa.text("UPDATE event_log SET tenant_id = :tenant WHERE id % 3 = 0 OR id BETWEEN :low AND :high"),
                {"tenant": tenant_id, "low": batch // 2, "high": batch + batch // 2},
            )
            conn.execute(
                sa.text("UPDATE graph_artifacts SET tenant_id = :tenant WHERE graph_id = 'gr_1'"), {"tenant": tenant_id}
            )
        assert all(None in _tenant_counts(engine, table) for table in ("event_log", "graph_artifacts"))

        runner.upgrade(BACKFILL)

        for table in ("event_log", "graph_artifacts"):
            assert _tenant_counts(engine, table) == {tenant_id: count}, table
    finally:
        engine.dispose()


def test_the_backfill_refuses_a_store_holding_two_tenants_and_changes_nothing(tmp_path: Path) -> None:
    db_url, engine = scratch_store(tmp_path)
    runner = migration_runner(HubConfig(root=tmp_path, db_url=db_url))
    try:
        runner.upgrade(TENANTS)
        with engine.begin() as conn:
            conn.execute(
                sa.text("INSERT INTO tenants VALUES ('ten_01J9Z3M0P8QK7V2S4W6X8Y0A1B', 'second', :at, 'migration')"),
                {"at": T0},
            )

        with pytest.raises(RuntimeError, match="needs exactly one"):
            runner.upgrade(BACKFILL)

        assert runner.current_revision() == TENANTS
        assert {table for table, keyed in keyed_tables(engine).items() if keyed} == {"invitations", "invitation_facts"}
    finally:
        engine.dispose()


def test_the_backfill_downgrade_removes_exactly_the_column_it_added(tmp_path: Path) -> None:
    db_url, engine = scratch_store(tmp_path)
    runner = migration_runner(HubConfig(root=tmp_path, db_url=db_url))
    try:
        runner.upgrade(BEFORE)
        seed_every_tenant_owned_table(engine)
        runner.upgrade(TENANTS)
        schema_before, counts_before = schema(engine), row_counts(engine)

        runner.upgrade(BACKFILL)
        runner.downgrade(TENANTS)

        assert schema(engine) == schema_before
        assert row_counts(engine) == counts_before
    finally:
        engine.dispose()

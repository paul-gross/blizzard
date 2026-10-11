"""The tenant keys revision rebuilds every table whose natural key changes keyed by its tenant: each row keeps its
tenant, the keys and indexes lead with ``tenant_id``, ``tenant_id`` is required and points at ``tenants``. A run that
stopped part way finishes; the downgrade refuses while two tenants exist and otherwise restores the store exactly.

Seeded from the store's reflected shape at the pinned revisions rather than ``src/blizzard/hub/store/schema.py``, whose
tables keep changing shape after this revision."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest
import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations
from alembic.script import ScriptDirectory

from blizzard.foundation.store.engine import create_engine_from_url
from blizzard.hub.config import HubConfig
from blizzard.hub.runtime import migration_runner
from blizzard.hub.store import MIGRATIONS_DIR
from tests.test_store_migrations import _schema_snapshot
from tests.test_tenant_carry_over_migration import _seed_every_tenant_owned_table

pytestmark = pytest.mark.component

_BEFORE_TENANTS = "20261008_1000_drop_work_item_proposals"
_BACKFILL = "20261008_1200_tenant_id_backfill"
_KEYS = "20261008_1300_tenant_keys"
_T0 = datetime(2026, 1, 1, tzinfo=UTC)
_SECOND_TENANT = "ten_01J9Z3M0P8QK7V2S4W6X8Y0A1B"

# Every table the revision rebuilds.
_REKEYED = frozenset(
    {
        "answer_deliveries",
        "chunk_work_refs",
        "garden_proposal_closures",
        "graphs",
        "keyed_locks",
        "question_answers",
        "questions",
        "repositories",
        "repository_lifecycle_facts",
        "routines",
        "secret_lifecycle_facts",
        "secrets",
        "transcript_event_derivations",
        "transcript_events",
        "transcript_segments",
        "work_item_sequence",
        "work_items",
        "work_source_lifecycle_facts",
        "work_sources",
    }
)


def _store(tmp_path: Path) -> tuple[str, sa.Engine]:
    db_url = f"sqlite:///{tmp_path / 'hub.db'}"
    return db_url, create_engine_from_url(db_url)


def _seeded_at_the_backfill(tmp_path: Path) -> tuple[str, sa.Engine]:
    """A store holding one row in every tenant-owned table, carried into the one tenant by the backfill."""
    db_url, engine = _store(tmp_path)
    runner = migration_runner(HubConfig(root=tmp_path, db_url=db_url))
    runner.upgrade(_BEFORE_TENANTS)
    _seed_every_tenant_owned_table(engine)
    runner.upgrade(_BACKFILL)
    return db_url, engine


def _rows(engine: sa.Engine) -> dict[str, list[str]]:
    """Every row of every table, as comparable text, in a stable order."""
    with engine.connect() as conn:
        return {
            table: sorted(
                repr(tuple(row)) for row in conn.execute(sa.select(sa.text("*")).select_from(sa.table(table)))
            )
            for table in sa.inspect(engine).get_table_names()
            if table != "alembic_version"
        }


def _snapshot(engine: sa.Engine) -> dict[str, object]:
    return _schema_snapshot(engine, exemptions={}, name_exemptions={})


def _tenant_ids(engine: sa.Engine) -> list[str]:
    with engine.connect() as conn:
        return list(conn.execute(sa.text("SELECT tenant_id FROM tenants")).scalars())


def _tenant_column_nullable(engine: sa.Engine, table: str) -> bool:
    return next(c["nullable"] for c in sa.inspect(engine).get_columns(table) if c["name"] == "tenant_id")


def test_the_upgrade_keeps_every_row_in_its_tenant_and_keys_each_table_by_it(tmp_path: Path) -> None:
    db_url, engine = _seeded_at_the_backfill(tmp_path)
    try:
        rows_before = _rows(engine)
        [tenant_id] = _tenant_ids(engine)

        migration_runner(HubConfig(root=tmp_path, db_url=db_url)).upgrade(_KEYS)

        assert _rows(engine) == rows_before
        inspector = sa.inspect(engine)
        with engine.connect() as conn:
            for table in _REKEYED:
                assert set(conn.execute(sa.text(f"SELECT tenant_id FROM {table}")).scalars()) == {tenant_id}, table
                assert not _tenant_column_nullable(engine, table), table
                assert ("fk_" + table + "_tenant_id", ["tenant_id"], "tenants") in [
                    (fk["name"], fk["constrained_columns"], fk["referred_table"])
                    for fk in inspector.get_foreign_keys(table)
                ], table
        assert inspector.get_pk_constraint("secrets") == {
            "name": "pk_secrets",
            "constrained_columns": ["tenant_id", "name"],
        }
        assert inspector.get_pk_constraint("questions")["constrained_columns"] == ["tenant_id", "question_id"]
        assert (
            "fk_question_answers_questions",
            ["tenant_id", "question_id"],
            "questions",
            ["tenant_id", "question_id"],
        ) in [
            (fk["name"], fk["constrained_columns"], fk["referred_table"], fk["referred_columns"])
            for fk in inspector.get_foreign_keys("question_answers")
        ]
        assert [u["column_names"] for u in inspector.get_unique_constraints("work_items")] == [
            ["tenant_id", "source", "ref"]
        ]
    finally:
        engine.dispose()


def _key_only(engine: sa.Engine, tables: set[str]) -> None:
    """Run the revision's upgrade over ``tables`` alone, leaving the store as a run stopped after keying them would."""
    script = ScriptDirectory(str(MIGRATIONS_DIR)).get_revision(_KEYS)
    assert script is not None
    module = script.module
    with (
        engine.begin() as conn,
        Operations.context(MigrationContext.configure(conn)),
        pytest.MonkeyPatch.context() as mp,
    ):
        mp.setattr(module, "_PLAN", tuple(plan for plan in module._PLAN if plan[0] in tables))
        module.upgrade()


def test_a_rerun_over_a_store_keyed_part_way_finishes(tmp_path: Path) -> None:
    """Re-entry: the tables a stopped run already keyed — a parent and its child among them — are skipped, the copy a
    stopped batch left behind is cleared, every other table is rebuilt, and the store ends exactly as a single run
    leaves it."""
    db_url, engine = _seeded_at_the_backfill(tmp_path)
    runner = migration_runner(HubConfig(root=tmp_path, db_url=db_url))
    try:
        rows_before = _rows(engine)
        _key_only(engine, {"secrets", "work_sources", "question_answers", "transcript_events"})
        with engine.begin() as conn:
            conn.execute(sa.text("CREATE TABLE _alembic_tmp_graphs (graph_id VARCHAR)"))  # a stopped copy's leftover
        assert sa.inspect(engine).get_pk_constraint("secrets")["name"] == "pk_secrets"
        assert _tenant_column_nullable(engine, "work_items")
        assert runner.current_revision() == _BACKFILL

        runner.upgrade(_KEYS)

        assert runner.current_revision() == _KEYS
        assert _rows(engine) == rows_before
        (tmp_path / "reference").mkdir()
        reference_url, reference = _seeded_at_the_backfill(tmp_path / "reference")
        try:
            migration_runner(HubConfig(root=tmp_path / "reference", db_url=reference_url)).upgrade(_KEYS)
            assert _snapshot(engine) == _snapshot(reference)
        finally:
            reference.dispose()
    finally:
        engine.dispose()


def test_the_upgrade_refuses_a_row_without_a_tenant_and_changes_nothing(tmp_path: Path) -> None:
    db_url, engine = _seeded_at_the_backfill(tmp_path)
    runner = migration_runner(HubConfig(root=tmp_path, db_url=db_url))
    try:
        with engine.begin() as conn:
            conn.execute(sa.text("UPDATE work_items SET tenant_id = NULL"))
        snapshot, rows = _snapshot(engine), _rows(engine)

        with pytest.raises(RuntimeError, match="work_items holds rows without a tenant_id"):
            runner.upgrade(_KEYS)

        assert runner.current_revision() == _BACKFILL
        assert _snapshot(engine) == snapshot
        assert _rows(engine) == rows
    finally:
        engine.dispose()


def test_the_downgrade_refuses_a_store_holding_two_tenants_and_changes_nothing(tmp_path: Path) -> None:
    db_url, engine = _seeded_at_the_backfill(tmp_path)
    runner = migration_runner(HubConfig(root=tmp_path, db_url=db_url))
    try:
        runner.upgrade(_KEYS)
        [first] = _tenant_ids(engine)
        with engine.begin() as conn:
            conn.execute(
                sa.text(f"INSERT INTO tenants VALUES ('{_SECOND_TENANT}', 'second', :at, 'migration')"), {"at": _T0}
            )
        snapshot, rows = _snapshot(engine), _rows(engine)

        with pytest.raises(RuntimeError, match="delete every tenant but one before downgrading") as refused:
            runner.downgrade(_BACKFILL)

        assert first in str(refused.value) and _SECOND_TENANT in str(refused.value)
        assert runner.current_revision() == _KEYS
        assert _snapshot(engine) == snapshot
        assert _rows(engine) == rows
    finally:
        engine.dispose()


def test_with_one_tenant_the_downgrade_restores_the_store_exactly(tmp_path: Path) -> None:
    db_url, engine = _seeded_at_the_backfill(tmp_path)
    runner = migration_runner(HubConfig(root=tmp_path, db_url=db_url))
    try:
        snapshot, rows = _snapshot(engine), _rows(engine)

        runner.upgrade(_KEYS)
        assert _snapshot(engine) != snapshot
        runner.downgrade(_BACKFILL)

        assert runner.current_revision() == _BACKFILL
        assert _snapshot(engine) == snapshot
        assert _rows(engine) == rows
    finally:
        engine.dispose()

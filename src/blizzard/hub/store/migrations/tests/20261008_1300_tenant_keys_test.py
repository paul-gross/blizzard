"""The ``20261008_1300_tenant_keys`` revision: every row stays in its tenant and each table is keyed
by it, a part-way rerun finishes, a tenantless row or a two-tenant downgrade is refused with no
change, and with one tenant the downgrade restores the store exactly. A legacy over-long
derivations index name is renamed on the way to head."""

from __future__ import annotations

from pathlib import Path

import pytest
import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations
from alembic.script import ScriptDirectory

from blizzard.foundation.store.engine import create_engine_from_url
from blizzard.hub import runtime as hub_runtime
from blizzard.hub.config import HubConfig
from blizzard.hub.runtime import migration_runner
from blizzard.hub.store import MIGRATIONS_DIR
from tests.config_inputs import init_hub
from tests.migration_support_tenant_keys_migration import (
    _BACKFILL,
    _SECOND_TENANT,
    _T0,
    _rows,
    _seeded_at_the_backfill,
    _snapshot,
    _tenant_column_nullable,
    _tenant_ids,
)

# The egress indexes under the name each carries at head; between their own revision and the tenant keys they carry
# the name that revision gave them.
_DERIVATIONS_HISTORICAL = "ix_transcript_event_derivations_derived_at_segment_id"

_KEYS = "20261008_1300_tenant_keys"

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


@pytest.mark.unit
def test_legacy_long_derivations_index_is_renamed_on_the_way_to_head(tmp_path: Path) -> None:
    """Hub-only — a store the egress revision once gave a 71-character index name reaches head holding the head name."""
    legacy = "ix_transcript_event_derivations_derived_at_segment_id_extractor_version"
    config = init_hub(tmp_path)  # upgrades to head
    runner = hub_runtime.migration_runner(config)
    runner.downgrade("20261004_1300_egress_events")

    engine = create_engine_from_url(config.db_url)
    try:
        with engine.begin() as conn:
            conn.execute(sa.text(f"DROP INDEX {_DERIVATIONS_HISTORICAL}"))
            conn.execute(
                sa.text(
                    f"CREATE INDEX {legacy} ON transcript_event_derivations (derived_at, segment_id, extractor_version)"
                )
            )
    finally:
        engine.dispose()

    runner.upgrade("head")

    engine = create_engine_from_url(config.db_url)
    try:
        names = {str(i["name"]) for i in sa.inspect(engine).get_indexes("transcript_event_derivations")}
    finally:
        engine.dispose()
    assert "ix_transcript_event_derivations_egress_order" in names
    assert legacy not in names


@pytest.mark.component
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


@pytest.mark.component
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


@pytest.mark.component
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


@pytest.mark.component
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


@pytest.mark.component
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

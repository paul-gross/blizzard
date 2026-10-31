"""The tenant tighten revision makes every tenant-owned table whose key stays as it is require its tenant: each row keeps
its tenant, ``tenant_id`` is required and points at ``tenants``, the listing indexes lead with ``tenant_id``. A run that
stopped part way finishes; the downgrade refuses while two tenants exist and otherwise restores the store exactly.

Seeded from the store's reflected shape at the pinned revisions rather than ``src/blizzard/hub/store/schema.py``, whose
tables keep changing shape after this revision."""

# pyright: standard

from __future__ import annotations

import warnings
from pathlib import Path

import pytest
import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations
from alembic.script import ScriptDirectory
from sqlalchemy.exc import IntegrityError, SAWarning

from blizzard.hub.config import HubConfig
from blizzard.hub.runtime import migration_runner
from blizzard.hub.store import MIGRATIONS_DIR
from tests.migration_support_tenant_keys_migration import (
    _SECOND_TENANT,
    _T0,
    _rows,
    _snapshot,
    _tenant_column_nullable,
    _tenant_ids,
)
from tests.migration_support_tenant_tighten_migration import _KEYS, _index_names, _seeded_at_the_keys

pytestmark = [pytest.mark.component]

_TIGHTEN = "20261008_1400_tenant_tighten"


def _untightened(engine: sa.Engine) -> set[str]:
    """Every table whose ``tenant_id`` is still optional."""
    inspector = sa.inspect(engine)
    return {
        table
        for table in inspector.get_table_names()
        if any(c["name"] == "tenant_id" and c["nullable"] for c in inspector.get_columns(table))
    }


def _tighten_only(engine: sa.Engine, tables: set[str]) -> None:
    """Run the revision's upgrade over ``tables`` alone, leaving the store as a run stopped after tightening them."""
    script = ScriptDirectory(str(MIGRATIONS_DIR)).get_revision(_TIGHTEN)
    assert script is not None
    module = script.module
    with (
        engine.begin() as conn,
        Operations.context(MigrationContext.configure(conn)),
        pytest.MonkeyPatch.context() as mp,
    ):
        mp.setattr(module, "_PLAN", tuple(plan for plan in module._PLAN if plan[0] in tables))
        module.upgrade()


def test_the_upgrade_keeps_every_row_in_its_tenant_and_requires_it_on_every_table(tmp_path: Path) -> None:
    db_url, engine = _seeded_at_the_keys(tmp_path)
    try:
        rows_before = _rows(engine)
        [tenant_id] = _tenant_ids(engine)
        loose = _untightened(engine)
        assert {"event_log", "findings", "runner_external_usage", "scopes"} <= loose

        with warnings.catch_warnings():
            warnings.simplefilter("error", SAWarning)
            migration_runner(HubConfig(root=tmp_path, db_url=db_url)).upgrade(_TIGHTEN)

        assert _rows(engine) == rows_before
        assert _untightened(engine) == set()
        inspector = sa.inspect(engine)
        with engine.connect() as conn:
            for table in loose:
                assert set(conn.execute(sa.text(f"SELECT tenant_id FROM {table}")).scalars()) == {tenant_id}, table
                assert ("fk_" + table + "_tenant_id", ["tenant_id"], "tenants") in [
                    (fk["name"], fk["constrained_columns"], fk["referred_table"])
                    for fk in inspector.get_foreign_keys(table)
                ], table
        assert {"ix_event_log_tenant_recorded_at"} <= _index_names(engine, "event_log")
        assert "ix_event_log_recorded_at" not in _index_names(engine, "event_log")
        assert {
            "ix_transitions_tenant_recorded_at_transition_id",
            "ix_transitions_recorded_at_transition_id",
        } <= _index_names(engine, "transitions")
        assert "ix_scopes_tenant_id" in _index_names(engine, "scopes")
        assert inspector.get_pk_constraint("runner_external_usage") == {
            "name": "pk_runner_external_usage",
            "constrained_columns": ["runner_id", "slug"],
        }
    finally:
        engine.dispose()


def test_after_the_upgrade_a_row_must_name_a_tenant_the_store_holds(tmp_path: Path) -> None:
    db_url, engine = _seeded_at_the_keys(tmp_path)
    try:
        migration_runner(HubConfig(root=tmp_path, db_url=db_url)).upgrade(_TIGHTEN)

        with pytest.raises(IntegrityError, match="NOT NULL"), engine.begin() as conn:
            conn.execute(sa.text("UPDATE event_log SET tenant_id = NULL"))
        with engine.begin() as conn:
            conn.execute(sa.text("UPDATE findings SET tenant_id = 'ten_unknown'"))
            violated = {row[2] for row in conn.execute(sa.text("PRAGMA foreign_key_check(findings)"))}
        assert "tenants" in violated
    finally:
        engine.dispose()


def test_a_rerun_over_a_store_tightened_part_way_finishes(tmp_path: Path) -> None:
    """Re-entry: the tables a stopped run already tightened are skipped, the copy a stopped batch left behind is
    cleared, every other table is tightened, and the store ends exactly as a single run leaves it."""
    db_url, engine = _seeded_at_the_keys(tmp_path)
    runner = migration_runner(HubConfig(root=tmp_path, db_url=db_url))
    try:
        rows_before = _rows(engine)
        _tighten_only(engine, {"event_log", "findings", "runner_external_usage"})
        with engine.begin() as conn:
            conn.execute(sa.text("CREATE TABLE _alembic_tmp_scopes (slug VARCHAR)"))  # a stopped copy's leftover
        assert not _tenant_column_nullable(engine, "event_log")
        assert _tenant_column_nullable(engine, "scopes")
        assert runner.current_revision() == _KEYS

        runner.upgrade(_TIGHTEN)

        assert runner.current_revision() == _TIGHTEN
        assert _rows(engine) == rows_before
        (tmp_path / "reference").mkdir()
        reference_url, reference = _seeded_at_the_keys(tmp_path / "reference")
        try:
            migration_runner(HubConfig(root=tmp_path / "reference", db_url=reference_url)).upgrade(_TIGHTEN)
            assert _snapshot(engine) == _snapshot(reference)
        finally:
            reference.dispose()
    finally:
        engine.dispose()


def test_the_upgrade_refuses_a_row_without_a_tenant_and_changes_nothing(tmp_path: Path) -> None:
    db_url, engine = _seeded_at_the_keys(tmp_path)
    runner = migration_runner(HubConfig(root=tmp_path, db_url=db_url))
    try:
        with engine.begin() as conn:
            conn.execute(sa.text("UPDATE usage_facts SET tenant_id = NULL"))
        snapshot, rows = _snapshot(engine), _rows(engine)

        with pytest.raises(RuntimeError, match="usage_facts holds rows without a tenant_id"):
            runner.upgrade(_TIGHTEN)

        assert runner.current_revision() == _KEYS
        assert _snapshot(engine) == snapshot
        assert _rows(engine) == rows
    finally:
        engine.dispose()


def test_the_downgrade_refuses_a_store_holding_two_tenants_and_changes_nothing(tmp_path: Path) -> None:
    db_url, engine = _seeded_at_the_keys(tmp_path)
    runner = migration_runner(HubConfig(root=tmp_path, db_url=db_url))
    try:
        runner.upgrade(_TIGHTEN)
        [first] = _tenant_ids(engine)
        with engine.begin() as conn:
            conn.execute(
                sa.text(f"INSERT INTO tenants VALUES ('{_SECOND_TENANT}', 'second', :at, 'migration')"), {"at": _T0}
            )
        snapshot, rows = _snapshot(engine), _rows(engine)

        with pytest.raises(RuntimeError, match="delete every tenant but one before downgrading") as refused:
            runner.downgrade(_KEYS)

        assert first in str(refused.value) and _SECOND_TENANT in str(refused.value)
        assert runner.current_revision() == _TIGHTEN
        assert _snapshot(engine) == snapshot
        assert _rows(engine) == rows
    finally:
        engine.dispose()


def test_with_one_tenant_the_downgrade_restores_the_store_exactly(tmp_path: Path) -> None:
    db_url, engine = _seeded_at_the_keys(tmp_path)
    runner = migration_runner(HubConfig(root=tmp_path, db_url=db_url))
    try:
        snapshot, rows = _snapshot(engine), _rows(engine)

        runner.upgrade(_TIGHTEN)
        assert _snapshot(engine) != snapshot
        with warnings.catch_warnings():
            warnings.simplefilter("error", SAWarning)
            runner.downgrade(_KEYS)

        assert runner.current_revision() == _KEYS
        assert _snapshot(engine) == snapshot
        assert _rows(engine) == rows
    finally:
        engine.dispose()

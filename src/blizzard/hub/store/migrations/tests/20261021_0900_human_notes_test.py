"""The human-notes revision adds the nullable note and requeuer columns, re-runs idempotently, and downgrades exactly."""

from __future__ import annotations

from pathlib import Path

import pytest
import sqlalchemy as sa

from blizzard.foundation.store.engine import create_engine_from_url
from blizzard.hub.store import schema as s
from tests.migration_support_store_migrations import _schema_snapshot
from tests.support import migrate_to

pytestmark = [pytest.mark.component]

_PARENT = "20261019_0900_runner_version"

_VERSION = "20261021_0900_human_notes"

_NEW = {
    "decision_resolutions": {"note"},
    "requeues": {"requeued_by", "note"},
    "chunk_restarts": {"note"},
}


def _columns(engine: sa.Engine, table: str) -> dict[str, bool]:
    return {c["name"]: bool(c["nullable"]) for c in sa.inspect(engine).get_columns(table)}


def test_the_upgrade_adds_every_column_nullable(tmp_path: Path) -> None:
    runner, engine = migrate_to(tmp_path, _PARENT)

    runner.upgrade(_VERSION)

    for table, names in _NEW.items():
        columns = _columns(engine, table)
        assert all(columns[name] for name in names), table


def test_the_new_columns_match_the_declared_schema(tmp_path: Path) -> None:
    _, engine = migrate_to(tmp_path, "head")
    declared = create_engine_from_url(f"sqlite:///{tmp_path / 'declared.db'}")
    try:
        s.metadata.create_all(declared)
        for table, names in _NEW.items():
            migrated, wanted = _columns(engine, table), _columns(declared, table)
            assert {name: migrated[name] for name in names} == {name: wanted[name] for name in names}
    finally:
        declared.dispose()


def test_a_rerun_over_a_store_already_reshaped_changes_nothing(tmp_path: Path) -> None:
    runner, engine = migrate_to(tmp_path, _VERSION)
    schema = _schema_snapshot(engine, exemptions={}, name_exemptions={})
    with engine.begin() as conn:
        conn.execute(sa.text(f"UPDATE alembic_version SET version_num = '{_PARENT}'"))

    runner.upgrade(_VERSION)

    assert _schema_snapshot(engine, exemptions={}, name_exemptions={}) == schema


def test_the_downgrade_restores_the_store_exactly(tmp_path: Path) -> None:
    runner, engine = migrate_to(tmp_path, _PARENT)
    before = _schema_snapshot(engine, exemptions={}, name_exemptions={})
    runner.upgrade(_VERSION)

    runner.downgrade(_PARENT)

    assert _schema_snapshot(engine, exemptions={}, name_exemptions={}) == before
    for table, names in _NEW.items():
        assert not names & set(_columns(engine, table))
    runner.upgrade(_VERSION)
    for table, names in _NEW.items():
        assert set(_columns(engine, table)) >= names

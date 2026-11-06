"""The cadences revision: four empty tables are created as ``src/blizzard/hub/store/schema.py`` declares them, a re-run
changes nothing, and the downgrade restores the parent store exactly, after which the upgrade runs again."""

from __future__ import annotations

from pathlib import Path

import pytest
import sqlalchemy as sa

from blizzard.foundation.store.engine import create_engine_from_url
from blizzard.hub.store import schema as s
from tests.support.migrations.store_migrations import schema_snapshot
from tests.support.scaffolding import migrate_to

pytestmark = [pytest.mark.component]

_PARENT = "20261105_1200_forge_references_required"
_CADENCES = "20261105_1300_cadences"
_CREATED = ("cadences", "cadence_chores", "cadence_chore_scopes", "cadence_lifecycle_facts")


def _snapshot(engine: sa.Engine) -> dict[str, object]:
    return schema_snapshot(engine, exemptions={}, name_exemptions={})


def _created(engine: sa.Engine) -> dict[str, object]:
    snapshot = _snapshot(engine)
    return {table: snapshot[table] for table in _CREATED}


def test_the_upgrade_creates_the_tables_as_the_schema_declares(tmp_path: Path) -> None:
    runner, engine = migrate_to(tmp_path, _PARENT)
    runner.upgrade(_CADENCES)
    declared = create_engine_from_url(f"sqlite:///{tmp_path / 'declared.db'}")
    try:
        s.metadata.create_all(declared)
        assert _created(engine) == _created(declared)
    finally:
        declared.dispose()
    with engine.connect() as conn:
        for table in _CREATED:
            assert conn.execute(sa.text(f"SELECT COUNT(*) FROM {table}")).scalar() == 0


def test_a_rerun_over_a_store_already_holding_the_tables_changes_nothing(tmp_path: Path) -> None:
    runner, engine = migrate_to(tmp_path, _CADENCES)
    schema = _snapshot(engine)
    with engine.begin() as conn:
        conn.execute(sa.text(f"UPDATE alembic_version SET version_num = '{_PARENT}'"))

    runner.upgrade(_CADENCES)

    assert _snapshot(engine) == schema


def test_the_downgrade_restores_the_parent_store_and_the_upgrade_runs_again(tmp_path: Path) -> None:
    runner, engine = migrate_to(tmp_path, _PARENT)
    before = _snapshot(engine)
    runner.upgrade(_CADENCES)
    after = _snapshot(engine)

    runner.downgrade(_PARENT)

    assert runner.current_revision() == _PARENT
    assert _snapshot(engine) == before
    runner.upgrade(_CADENCES)
    assert runner.current_revision() == _CADENCES
    assert _snapshot(engine) == after

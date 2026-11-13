"""The credentials revision: the three credential tables and the two nullable ``credential_id`` columns come out as
``src/blizzard/hub/store/schema.py`` declares them; a re-run changes nothing; and the downgrade restores the parent
store exactly, after which the upgrade runs again."""

from __future__ import annotations

from pathlib import Path

import pytest
import sqlalchemy as sa

from blizzard.foundation.store.engine import create_engine_from_url
from blizzard.hub.store import schema
from tests.support.migrations.store_migrations import schema_snapshot
from tests.support.scaffolding import migrate_to

pytestmark = [pytest.mark.component]

_PARENT = "20261113_0900_repair_hub_locks"
_REVISION = "20261113_0900_credentials"
_TOUCHED = (
    "credentials",
    "credential_former_slugs",
    "credential_lifecycle_facts",
    "repositories",
    "work_sources",
)


def _snapshot(engine: sa.Engine) -> dict[str, object]:
    return schema_snapshot(engine, exemptions={}, name_exemptions={})


def test_the_touched_tables_match_the_declared_schema(tmp_path: Path) -> None:
    _, engine = migrate_to(tmp_path, _REVISION)
    declared = create_engine_from_url(f"sqlite:///{tmp_path / 'declared.db'}")
    try:
        schema.metadata.create_all(declared)
        migrated, wanted = _snapshot(engine), _snapshot(declared)
    finally:
        declared.dispose()

    assert {table: migrated[table] for table in _TOUCHED} == {table: wanted[table] for table in _TOUCHED}


def test_a_rerun_over_a_store_already_reshaped_changes_nothing(tmp_path: Path) -> None:
    runner, engine = migrate_to(tmp_path, _REVISION)
    reshaped = _snapshot(engine)
    with engine.begin() as conn:
        conn.execute(sa.text(f"UPDATE alembic_version SET version_num = '{_PARENT}'"))

    runner.upgrade(_REVISION)

    assert _snapshot(engine) == reshaped


def test_the_downgrade_restores_the_store_exactly(tmp_path: Path) -> None:
    runner, engine = migrate_to(tmp_path, _PARENT)
    before = _snapshot(engine)
    runner.upgrade(_REVISION)
    after = _snapshot(engine)

    runner.downgrade(_PARENT)

    assert _snapshot(engine) == before
    runner.upgrade(_REVISION)
    assert _snapshot(engine) == after

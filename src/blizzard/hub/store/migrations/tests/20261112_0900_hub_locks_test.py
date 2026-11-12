"""The hub-locks revision: ``hub_locks`` is created as ``src/blizzard/hub/store/schema.py`` declares it and seeded with
one row per hub lock; a re-run changes nothing; and the downgrade restores the parent store exactly, after which the
upgrade runs again."""

from __future__ import annotations

from pathlib import Path

import pytest
import sqlalchemy as sa

from blizzard.hub.domain.kernel.locks import HubLock
from tests.support.migrations.store_migrations import schema_snapshot
from tests.support.scaffolding import migrate_to

pytestmark = [pytest.mark.component]

_PARENT = "20261111_1000_chunk_repository_targets"
_REVISION = "20261112_0900_hub_locks"
_TABLE = "hub_locks"


def _snapshot(engine: sa.Engine) -> dict[str, object]:
    return schema_snapshot(engine, exemptions={}, name_exemptions={})


def _rows(engine: sa.Engine) -> list[str]:
    with engine.connect() as conn:
        return sorted(conn.execute(sa.text(f"SELECT name FROM {_TABLE}")).scalars())


def test_the_table_is_seeded_with_one_row_per_hub_lock(tmp_path: Path) -> None:
    _, engine = migrate_to(tmp_path, _REVISION)

    assert _rows(engine) == sorted(lock.value for lock in HubLock)


def test_a_rerun_over_a_store_already_reshaped_changes_nothing(tmp_path: Path) -> None:
    runner, engine = migrate_to(tmp_path, _REVISION)
    schema = _snapshot(engine)
    with engine.begin() as conn:
        conn.execute(sa.text(f"UPDATE alembic_version SET version_num = '{_PARENT}'"))

    runner.upgrade(_REVISION)

    assert _snapshot(engine) == schema
    assert _rows(engine) == sorted(lock.value for lock in HubLock)


def test_the_downgrade_restores_the_store_exactly(tmp_path: Path) -> None:
    runner, engine = migrate_to(tmp_path, _PARENT)
    before = _snapshot(engine)
    runner.upgrade(_REVISION)

    runner.downgrade(_PARENT)

    assert _snapshot(engine) == before
    runner.upgrade(_REVISION)
    assert _rows(engine) == sorted(lock.value for lock in HubLock)

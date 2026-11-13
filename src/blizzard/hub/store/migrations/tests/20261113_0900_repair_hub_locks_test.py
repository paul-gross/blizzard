"""Repair hub locks skipped by stores stamped before the migration chain was reordered."""

from __future__ import annotations

from pathlib import Path

import pytest
import sqlalchemy as sa

from blizzard.hub.domain.kernel.locks import HubLock
from blizzard.hub.store.locking import lock_hub
from tests.support.migrations.store_migrations import schema_snapshot
from tests.support.scaffolding import migrate_to

pytestmark = pytest.mark.component

_PARENT = "20261112_0900_repair_repository_targets"
_OLD_CI = "20261111_0900_allow_no_ci"
_REVISION = "20261113_0900_repair_hub_locks"


def _snapshot(engine: sa.Engine) -> dict[str, object]:
    return schema_snapshot(engine, exemptions={}, name_exemptions={})


def _assert_locks(engine: sa.Engine) -> None:
    with engine.begin() as conn:
        for lock in HubLock:
            lock_hub(conn, lock)
        assert set(conn.execute(sa.text("SELECT name FROM hub_locks")).scalars()) >= {lock.value for lock in HubLock}


@pytest.mark.parametrize("old_revision", [_OLD_CI, _PARENT])
def test_old_stamped_store_is_repaired(tmp_path: Path, old_revision: str) -> None:
    runner, engine = migrate_to(tmp_path, old_revision)
    with engine.begin() as conn:
        conn.execute(sa.text("DROP TABLE hub_locks"))
        if old_revision == _OLD_CI:
            conn.execute(sa.text("DROP TABLE chunk_repository_targets"))
            conn.execute(sa.text("ALTER TABLE project_repository_links DROP COLUMN base_branch"))
        conn.execute(
            sa.text(
                "INSERT INTO project_repository_links (project_id, repository_id, revision, tenant_id) "
                "VALUES ('project', 'repo', 1, 'tenant')"
            )
        )

    runner.upgrade("head")
    _assert_locks(engine)
    with engine.connect() as conn:
        assert conn.execute(sa.text("SELECT project_id, base_branch FROM project_repository_links")).all() == [
            ("project", None)
        ]
        assert sa.inspect(conn).has_table("chunk_repository_targets")

    runner.downgrade(_PARENT)
    _assert_locks(engine)
    runner.upgrade("head")
    _assert_locks(engine)


def test_existing_table_and_rows_survive_upgrade_downgrade_and_rerun(tmp_path: Path) -> None:
    runner, engine = migrate_to(tmp_path, _PARENT)
    with engine.begin() as conn:
        conn.execute(sa.text("INSERT INTO hub_locks (name) VALUES ('extra')"))
        conn.execute(sa.text("DELETE FROM hub_locks WHERE name = 'identity'"))
    before = _snapshot(engine)

    runner.upgrade(_REVISION)
    _assert_locks(engine)
    with engine.connect() as conn:
        assert conn.execute(sa.text("SELECT name FROM hub_locks WHERE name = 'extra'")).scalar_one() == "extra"
    runner.downgrade(_PARENT)
    assert _snapshot(engine) == before
    runner.upgrade(_REVISION)
    _assert_locks(engine)
    assert _snapshot(engine) == before


def test_fresh_store_has_usable_locks(tmp_path: Path) -> None:
    _, engine = migrate_to(tmp_path, "head")
    _assert_locks(engine)

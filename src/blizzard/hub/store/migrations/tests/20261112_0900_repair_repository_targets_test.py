"""Repair stores stamped at the CI revision without its repository-target ancestors."""

from __future__ import annotations

from pathlib import Path

import pytest
import sqlalchemy as sa

from tests.support.migrations.store_migrations import schema_snapshot
from tests.support.scaffolding import migrate_to

pytestmark = pytest.mark.component

_PARENT = "20261111_0900_allow_no_ci"
_REVISION = "20261112_0900_repair_repository_targets"
_TABLE = "chunk_repository_targets"


def _snapshot(engine: sa.Engine) -> dict[str, object]:
    return schema_snapshot(engine, exemptions={}, name_exemptions={})


def test_repair_old_shape_and_repeat(tmp_path: Path) -> None:
    runner, engine = migrate_to(tmp_path, _PARENT)
    expected = _snapshot(engine)
    with engine.begin() as conn:
        conn.execute(sa.text(f"DROP TABLE {_TABLE}"))
        conn.execute(sa.text("ALTER TABLE project_repository_links DROP COLUMN base_branch"))
        conn.execute(
            sa.text(
                "INSERT INTO project_repository_links (project_id, repository_id, revision, tenant_id) "
                "VALUES ('project', 'repo', 1, 'tenant')"
            )
        )

    runner.upgrade(_REVISION)
    assert _snapshot(engine) == expected
    with engine.connect() as conn:
        assert conn.execute(sa.text("SELECT base_branch FROM project_repository_links")).all() == [(None,)]

    with engine.begin() as conn:
        conn.execute(sa.text("UPDATE alembic_version SET version_num = :parent"), {"parent": _PARENT})
    runner.upgrade(_REVISION)
    assert _snapshot(engine) == expected


def test_normal_store_and_downgrade_preserve_schema_and_data(tmp_path: Path) -> None:
    runner, engine = migrate_to(tmp_path, _PARENT)
    with engine.begin() as conn:
        conn.execute(
            sa.text(
                "INSERT INTO chunk_repository_targets "
                "(chunk_id, repository_id, base_branch, inherited, captured_at, tenant_id) "
                "VALUES ('chunk', 'repo', 'main', 1, '2026-11-12 00:00:00', 'tenant')"
            )
        )
    before = _snapshot(engine)
    runner.upgrade(_REVISION)
    runner.downgrade(_PARENT)
    assert _snapshot(engine) == before
    with engine.connect() as conn:
        assert conn.execute(sa.text(f"SELECT base_branch FROM {_TABLE}")).scalar_one() == "main"
    runner.upgrade(_REVISION)
    assert _snapshot(engine) == before

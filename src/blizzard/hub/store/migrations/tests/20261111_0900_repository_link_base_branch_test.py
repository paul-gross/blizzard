"""The repository-link-base-branch revision: ``project_repository_links`` gains a nullable ``base_branch`` as
``src/blizzard/hub/store/schema.py`` declares it; an existing link survives reading null, so it inherits; a re-run
changes nothing; and the downgrade restores the parent store exactly, after which the upgrade runs again."""

from __future__ import annotations

from pathlib import Path

import pytest
import sqlalchemy as sa

from blizzard.foundation.store.engine import create_engine_from_url
from blizzard.hub.store import schema as s
from tests.support.migrations.store_migrations import schema_snapshot
from tests.support.scaffolding import migrate_to

pytestmark = [pytest.mark.component]

_PARENT = "20261109_0900_urgency_references"
_REVISION = "20261111_0900_repository_link_base_branch"


def _snapshot(engine: sa.Engine) -> dict[str, object]:
    return schema_snapshot(engine, exemptions={}, name_exemptions={})


def _columns(engine: sa.Engine) -> dict[str, bool]:
    return {c["name"]: bool(c["nullable"]) for c in sa.inspect(engine).get_columns("project_repository_links")}


def test_an_existing_link_reads_a_null_base_branch(tmp_path: Path) -> None:
    runner, engine = migrate_to(tmp_path, _PARENT)
    with engine.begin() as conn:
        conn.execute(sa.text("PRAGMA foreign_keys = OFF"))
        conn.execute(
            sa.text(
                "INSERT INTO project_repository_links (project_id, repository_id, revision, tenant_id)"
                " VALUES ('prj_1', 'repo_1', 1, 'tenant_1')"
            )
        )

    runner.upgrade(_REVISION)

    assert _columns(engine)["base_branch"] is True
    with engine.connect() as conn:
        held = conn.execute(sa.text("SELECT base_branch FROM project_repository_links")).scalars().all()
    assert held == [None]


def test_the_new_column_matches_the_declared_schema(tmp_path: Path) -> None:
    _, engine = migrate_to(tmp_path, "head")
    declared = create_engine_from_url(f"sqlite:///{tmp_path / 'declared.db'}")
    try:
        s.metadata.create_all(declared)
        assert _columns(engine)["base_branch"] == _columns(declared)["base_branch"]
    finally:
        declared.dispose()


def test_a_rerun_over_a_store_already_reshaped_changes_nothing(tmp_path: Path) -> None:
    runner, engine = migrate_to(tmp_path, _REVISION)
    schema = _snapshot(engine)
    with engine.begin() as conn:
        conn.execute(sa.text(f"UPDATE alembic_version SET version_num = '{_PARENT}'"))

    runner.upgrade(_REVISION)

    assert _snapshot(engine) == schema


def test_the_downgrade_restores_the_store_exactly(tmp_path: Path) -> None:
    runner, engine = migrate_to(tmp_path, _PARENT)
    before = _snapshot(engine)
    runner.upgrade(_REVISION)

    runner.downgrade(_PARENT)

    assert _snapshot(engine) == before
    runner.upgrade(_REVISION)
    assert "base_branch" in _columns(engine)

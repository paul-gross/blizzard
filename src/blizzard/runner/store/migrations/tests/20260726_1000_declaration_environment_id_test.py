"""Tests proving the ``20260726_1000_declaration_environment_id`` revision."""

from __future__ import annotations

from pathlib import Path

import pytest
import sqlalchemy as sa

from blizzard.foundation.store.engine import create_engine_from_url
from blizzard.runner import runtime as runner_runtime
from tests.migration_support_store_migrations import assert_frozen_table_lacks_a_later_revisions_columns


@pytest.mark.component
def test_declaration_environment_id_migration_discards_the_pre_revision_rows(tmp_path: Path) -> None:
    """``20260726_1000`` drops and recreates ``git_commit_declarations`` — no
    back-fill; a row seeded at the parent revision must not survive the upgrade."""
    parent = "20260725_1200_runner_check_results"
    config = runner_runtime.init_environment(tmp_path)
    migrations = runner_runtime.migration_runner(config)
    migrations.downgrade(parent)

    engine = create_engine_from_url(config.db_url)
    try:
        with engine.begin() as conn:
            conn.execute(
                sa.text(
                    "INSERT INTO git_commit_declarations"
                    ' (lease_id, chunk_id, node_id, epoch, forge, repo, branch, "commit", declared_at)'
                    " VALUES ('lease_1', 'ch_1', 'nd_build', 1, 'github', 'r', 'b', 'abc', '2026-07-01 00:00:00')"
                )
            )

        migrations.upgrade("head")

        with engine.connect() as conn:
            surviving = conn.execute(sa.text("SELECT count(*) FROM git_commit_declarations")).scalar_one()
        columns = {c["name"] for c in sa.inspect(engine).get_columns("git_commit_declarations")}
    finally:
        engine.dispose()

    assert surviving == 0
    assert "environment_id" in columns
    assert "forge" not in columns


@pytest.mark.parametrize(
    "tree,parent_revision,table,columns,direction",
    [
        pytest.param(
            "runner",
            "20260725_1200_runner_check_results",
            "git_commit_declarations",
            ("environment_id",),
            "added",
            id="runner.git_commit_declarations.environment_id",
        ),
        pytest.param(
            "runner",
            "20260725_1200_runner_check_results",
            "git_commit_declarations",
            ("forge",),
            "removed",
            id="runner.git_commit_declarations.forge.removed",
        ),
    ],
)
@pytest.mark.unit
def test_frozen_table_lacks_a_later_revisions_columns(
    tree: str, parent_revision: str, table: str, columns: tuple[str, ...], direction: str, tmp_path: Path
) -> None:
    assert_frozen_table_lacks_a_later_revisions_columns(tree, parent_revision, table, columns, direction, tmp_path)

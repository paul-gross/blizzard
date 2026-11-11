"""The opt-in defaults old repositories and captured terms to strict CI."""

from pathlib import Path

import pytest
import sqlalchemy as sa

from tests.support.scaffolding import migrate_to

pytestmark = [pytest.mark.component]
_PARENT = "20261109_0900_urgency_references"
_REVISION = "20261111_0900_allow_no_ci"


def test_upgrade_defaults_existing_rows_and_is_reversible(tmp_path: Path) -> None:
    runner, engine = migrate_to(tmp_path, _PARENT)
    with engine.begin() as conn:
        conn.execute(sa.text("PRAGMA foreign_keys = OFF"))
        conn.execute(
            sa.text(
                "INSERT INTO repositories (repository_id, slug, name, owner, repo, base_branch, secret_name, revision, created_at, created_by, tenant_id, forge_id) VALUES ('r', 'r', 'r', 'o', 'r', 'main', 's', 1, '2026-11-11', 'u', 't', 'f')"
            )
        )
        conn.execute(
            sa.text(
                "INSERT INTO flow_submission_terms (submission_id, tenant_id, repository_id, landing_policy) VALUES ('s', 't', 'r', 'merge-on-checks')"
            )
        )
    runner.upgrade(_REVISION)
    with engine.connect() as conn:
        for table in ("repositories", "flow_submission_terms"):
            assert conn.execute(sa.text(f"SELECT allow_no_ci FROM {table}")).scalar_one() == 0
    runner.downgrade(_PARENT)
    runner.upgrade(_REVISION)

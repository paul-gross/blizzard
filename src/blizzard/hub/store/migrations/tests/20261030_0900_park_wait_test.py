"""The schema-neutral boundary rejects incompatible data and settles closed waits on rollback."""

from __future__ import annotations

from pathlib import Path

import pytest
import sqlalchemy as sa

from tests.support import migrate_to

pytestmark = [pytest.mark.component]

_PARENT = "20261029_1200_landing_policies"
_REVISION = "20261030_0900_park_wait"


def test_non_default_repository_refuses_downgrade_before_poll_cleanup(tmp_path: Path) -> None:
    runner, engine = migrate_to(tmp_path, _PARENT)
    runner.upgrade(_REVISION)
    with engine.begin() as conn:
        conn.execute(sa.text("PRAGMA foreign_keys = OFF"))
        conn.execute(
            sa.text(
                "INSERT INTO repositories (repository_id, slug, name, forge_api_url, owner, repo, base_branch,"
                " secret_name, revision, created_at, created_by, tenant_id, landing_policy)"
                " VALUES ('repo_park', 'repo_park', 'repo_park', 'https://api.github.com', 'acme', 'repo_park',"
                " 'main', 's', 1, '2026-10-30', 'op', 'tenant_1', 'park')"
            )
        )
        conn.execute(
            sa.text(
                "INSERT INTO hub_node_poll (chunk_id, node_id, epoch, polled_at, tenant_id, kind)"
                " VALUES ('chunk_1', 'node_1', 1, '2026-10-30', 'tenant_1', 'awaiting-merge')"
            )
        )

    with pytest.raises(RuntimeError, match="repo_park"):
        runner.downgrade(_PARENT)
    with engine.connect() as conn:
        assert conn.execute(sa.text("SELECT count(*) FROM hub_node_poll")).scalar_one() == 1
    with engine.begin() as conn:
        conn.execute(sa.text("UPDATE repositories SET landing_policy = 'merge-on-checks'"))

    runner.downgrade(_PARENT)
    with engine.connect() as conn:
        assert conn.execute(sa.text("SELECT count(*) FROM hub_node_poll")).scalar_one() == 0
    runner.upgrade(_REVISION)

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


def test_open_terms_refuse_downgrade_until_closed_then_are_removed(tmp_path: Path) -> None:
    runner, engine = migrate_to(tmp_path, _REVISION)
    with engine.begin() as conn:
        conn.execute(sa.text("PRAGMA foreign_keys = OFF"))
        conn.execute(
            sa.text(
                "INSERT INTO flow_submissions (submission_id, chunk_id, node_id, epoch, generation, arrival,"
                " submitted_at, tenant_id) VALUES ('sub_1', 'chunk_park', 'node_1', 1, 1, 1,"
                " '2026-10-30', 'tenant_1')"
            )
        )
        conn.execute(
            sa.text(
                "INSERT INTO flow_submission_terms (submission_id, tenant_id, repository_id, landing_policy)"
                " VALUES ('sub_1', 'tenant_1', 'repo_park', 'park')"
            )
        )
        conn.execute(
            sa.text(
                "INSERT INTO hub_node_poll (chunk_id, node_id, epoch, polled_at, tenant_id, kind)"
                " VALUES ('chunk_park', 'node_1', 1, '2026-10-30', 'tenant_1', 'awaiting-merge')"
            )
        )

    with pytest.raises(RuntimeError, match="chunk_park"):
        runner.downgrade(_PARENT)
    with engine.connect() as conn:
        assert conn.execute(sa.text("SELECT count(*) FROM flow_submission_terms")).scalar_one() == 1
        assert conn.execute(sa.text("SELECT count(*) FROM hub_node_poll")).scalar_one() == 1
    with engine.begin() as conn:
        conn.execute(sa.text("PRAGMA foreign_keys = OFF"))
        conn.execute(
            sa.text(
                "INSERT INTO flow_submission_closures (submission_id, outcome, closed_at, tenant_id)"
                " VALUES ('sub_1', 'stopped', '2026-10-30', 'tenant_1')"
            )
        )

    runner.downgrade(_PARENT)
    with engine.connect() as conn:
        assert conn.execute(sa.text("SELECT count(*) FROM flow_submission_terms")).scalar_one() == 0
        assert conn.execute(sa.text("SELECT count(*) FROM hub_node_poll")).scalar_one() == 0
    runner.upgrade(_REVISION)


def test_upgrade_reports_stored_reserved_graph_choice(tmp_path: Path) -> None:
    runner, engine = migrate_to(tmp_path, _PARENT)
    with engine.begin() as conn:
        conn.execute(sa.text("PRAGMA foreign_keys = OFF"))
        conn.execute(
            sa.text(
                "INSERT INTO graphs (graph_id, name, entry_node_id, definition_yaml, created_at, tenant_id)"
                " VALUES ('graph_1', 'delivery', 'node_1', '{}', '2026-10-30', 'tenant_1')"
            )
        )
        conn.execute(
            sa.text(
                "INSERT INTO graph_nodes (node_id, graph_id, name, executor, session, judged_by, tenant_id)"
                " VALUES ('node_1', 'graph_1', 'deliver', 'hub', 'fresh', 'worker', 'tenant_1')"
            )
        )
        conn.execute(
            sa.text(
                "INSERT INTO graph_choices (choice_id, node_id, name, description, tenant_id)"
                " VALUES ('choice_1', 'node_1', 'awaiting-merge', 'reserved', 'tenant_1')"
            )
        )
    with pytest.raises(RuntimeError, match=r"graph_1.*delivery.*tenant_1"):
        runner.upgrade(_REVISION)

"""The landing-policies revision: ``repositories`` and ``hub_node_poll`` each gain a tightened column that keeps its
server default and a nullable one, and two empty tables are created, all as ``src/blizzard/hub/store/schema.py``
declares them; a re-run changes nothing; a row written without either tightened column reads its default; and the
downgrade restores the parent store exactly."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest
import sqlalchemy as sa

from blizzard.foundation.store.engine import create_engine_from_url
from blizzard.hub.store import schema as s
from tests.migration_support_store_migrations import schema_snapshot
from tests.support import migrate_to

pytestmark = [pytest.mark.component]

_PARENT = "20261029_0900_close_attempt_resume_at"

_POLICIES = "20261029_1200_landing_policies"

_RESHAPED = ("repositories", "hub_node_poll", "project_repository_positions", "flow_submission_terms")

_AT = datetime(2026, 10, 29, tzinfo=UTC).isoformat()

#: More rows than one fill page, so the fill walks past its first batch.
_SEEDED = 1001


def _snapshot(engine: sa.Engine) -> dict[str, object]:
    return schema_snapshot(engine, exemptions={}, name_exemptions={})


def _reshaped(engine: sa.Engine) -> dict[str, object]:
    snapshot = _snapshot(engine)
    return {table: snapshot[table] for table in _RESHAPED}


def _columns(engine: sa.Engine, table: str) -> dict[str, bool]:
    return {c["name"]: bool(c["nullable"]) for c in sa.inspect(engine).get_columns(table)}


def _seed_repository(conn: sa.Connection, repository_id: str) -> None:
    conn.execute(
        sa.text(
            "INSERT INTO repositories (repository_id, slug, name, forge_api_url, owner, repo, base_branch,"
            " secret_name, revision, created_at, created_by, tenant_id)"
            " VALUES (:id, :id, :id, 'https://api.github.com', 'acme', :id, 'main', 's', 1, :at, 'op', 't')"
        ),
        {"id": repository_id, "at": _AT},
    )


def _seed_poll(conn: sa.Connection) -> None:
    conn.execute(
        sa.text(
            "INSERT INTO hub_node_poll (chunk_id, node_id, epoch, polled_at, tenant_id)"
            " VALUES ('ch_1', 'nd_deliver', 1, :at, 't')"
        ),
        {"at": _AT},
    )


def test_the_upgrade_reshapes_the_store_as_the_schema_declares(tmp_path: Path) -> None:
    runner, engine = migrate_to(tmp_path, _PARENT)
    runner.upgrade(_POLICIES)
    declared = create_engine_from_url(f"sqlite:///{tmp_path / 'declared.db'}")
    try:
        s.metadata.create_all(declared)
        assert _reshaped(engine) == _reshaped(declared)
    finally:
        declared.dispose()
    assert _columns(engine, "repositories")["landing_policy"] is False
    assert _columns(engine, "hub_node_poll")["kind"] is False


def test_the_upgrade_fills_every_seeded_row_and_keeps_both_defaults(tmp_path: Path) -> None:
    runner, engine = migrate_to(tmp_path, _PARENT)
    with engine.begin() as conn:
        conn.execute(sa.text("PRAGMA foreign_keys = OFF"))
        for n in range(_SEEDED):
            _seed_repository(conn, f"repo_{n:04d}")
            _seed_poll(conn)

    runner.upgrade(_POLICIES)

    with engine.begin() as conn:
        conn.execute(sa.text("PRAGMA foreign_keys = OFF"))
        assert conn.execute(sa.text("SELECT DISTINCT landing_policy FROM repositories")).scalars().all() == [
            "merge-on-checks"
        ]
        assert conn.execute(sa.text("SELECT DISTINCT kind FROM hub_node_poll")).scalars().all() == ["pending"]
        assert conn.execute(sa.text("SELECT COUNT(*) FROM repositories WHERE park_timeout_hours IS NULL")).scalar() == (
            _SEEDED
        )
        assert conn.execute(sa.text("SELECT COUNT(*) FROM hub_node_poll WHERE last_polled_at IS NULL")).scalar() == (
            _SEEDED
        )
        # A row written without either tightened column, as an older writer would, reads its default.
        _seed_repository(conn, "repo_late")
        _seed_poll(conn)
        assert (
            conn.execute(sa.text("SELECT landing_policy FROM repositories WHERE repository_id = 'repo_late'")).scalar()
            == "merge-on-checks"
        )
        assert conn.execute(sa.text("SELECT kind FROM hub_node_poll ORDER BY id DESC LIMIT 1")).scalar() == "pending"


def test_a_rerun_over_a_store_already_reshaped_changes_nothing(tmp_path: Path) -> None:
    runner, engine = migrate_to(tmp_path, _POLICIES)
    schema = _snapshot(engine)
    with engine.begin() as conn:
        conn.execute(sa.text(f"UPDATE alembic_version SET version_num = '{_PARENT}'"))

    runner.upgrade(_POLICIES)

    assert _snapshot(engine) == schema


def test_the_downgrade_restores_the_store_exactly(tmp_path: Path) -> None:
    runner, engine = migrate_to(tmp_path, _PARENT)
    before = _snapshot(engine)
    runner.upgrade(_POLICIES)
    with engine.begin() as conn:
        conn.execute(sa.text("PRAGMA foreign_keys = OFF"))
        _seed_repository(conn, "repo_kept")
        _seed_poll(conn)

    runner.downgrade(_PARENT)

    assert _snapshot(engine) == before
    with engine.connect() as conn:
        assert conn.execute(sa.text("SELECT repository_id FROM repositories")).scalars().all() == ["repo_kept"]
        assert conn.execute(sa.text("SELECT COUNT(*) FROM hub_node_poll")).scalar() == 1
    runner.upgrade(_POLICIES)
    assert _reshaped(engine).keys() == set(_RESHAPED)
    assert _columns(engine, "repositories")["landing_policy"] is False

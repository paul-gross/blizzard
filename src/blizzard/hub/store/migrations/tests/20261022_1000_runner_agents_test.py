"""The runner-agents revision: ``runner_registrations`` gains a nullable ``agents`` column, every existing runner
reading as having declared none; a re-run changes nothing; and the downgrade restores the store exactly."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest
import sqlalchemy as sa

from blizzard.foundation.store.engine import create_engine_from_url
from blizzard.hub.store import schema as s
from tests.migration_support_store_migrations import _schema_snapshot
from tests.support import default_tenant, migrate_to

pytestmark = [pytest.mark.component]

_PARENT = "20261021_0900_human_notes"

_AGENTS = "20261022_1000_runner_agents"

_AT = datetime(2026, 10, 22, tzinfo=UTC)


def _runner(engine: sa.Engine, tenant: str) -> None:
    with engine.begin() as conn:
        conn.execute(
            sa.text(
                "INSERT INTO runner_registrations (runner_id, name, added_at, tenant_id)"
                " VALUES ('rn_a', 'a', :at, :tenant)"
            ),
            {"at": _AT, "tenant": tenant},
        )


def _columns(engine: sa.Engine, table: str) -> dict[str, bool]:
    return {c["name"]: bool(c["nullable"]) for c in sa.inspect(engine).get_columns(table)}


def test_the_upgrade_adds_the_column_and_leaves_every_runner_declaring_none(tmp_path: Path) -> None:
    runner, engine = migrate_to(tmp_path, _PARENT)
    _runner(engine, default_tenant(engine))

    runner.upgrade(_AGENTS)

    assert _columns(engine, "runner_registrations")["agents"] is True
    with engine.connect() as conn:
        assert conn.execute(sa.text("SELECT agents FROM runner_registrations")).scalar_one() is None


def test_the_column_matches_the_declared_schema(tmp_path: Path) -> None:
    _, engine = migrate_to(tmp_path, "head")
    declared = create_engine_from_url(f"sqlite:///{tmp_path / 'declared.db'}")
    try:
        s.metadata.create_all(declared)
        assert _columns(engine, "runner_registrations") == _columns(declared, "runner_registrations")
    finally:
        declared.dispose()


def test_a_rerun_over_a_store_already_reshaped_changes_nothing(tmp_path: Path) -> None:
    runner, engine = migrate_to(tmp_path, _AGENTS)
    schema = _schema_snapshot(engine, exemptions={}, name_exemptions={})
    with engine.begin() as conn:
        conn.execute(sa.text(f"UPDATE alembic_version SET version_num = '{_PARENT}'"))

    runner.upgrade(_AGENTS)

    assert _schema_snapshot(engine, exemptions={}, name_exemptions={}) == schema


def test_the_downgrade_restores_the_store_exactly(tmp_path: Path) -> None:
    runner, engine = migrate_to(tmp_path, _PARENT)
    before = _schema_snapshot(engine, exemptions={}, name_exemptions={})
    _runner(engine, default_tenant(engine))
    runner.upgrade(_AGENTS)

    runner.downgrade(_PARENT)

    assert _schema_snapshot(engine, exemptions={}, name_exemptions={}) == before
    assert "agents" not in _columns(engine, "runner_registrations")
    with engine.connect() as conn:
        assert conn.execute(sa.text("SELECT runner_id FROM runner_registrations")).scalars().all() == ["rn_a"]
    runner.upgrade(_AGENTS)
    assert "agents" in _columns(engine, "runner_registrations")

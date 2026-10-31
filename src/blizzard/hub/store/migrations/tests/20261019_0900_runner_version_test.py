"""The runner-version revision adds the nullable judgement columns, re-runs idempotently, and downgrades exactly."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest
import sqlalchemy as sa

from blizzard.foundation.store.engine import create_engine_from_url
from blizzard.hub.store import schema as s
from tests.migration_support_store_migrations import schema_snapshot
from tests.support import default_tenant, migrate_to

pytestmark = [pytest.mark.component]

_PARENT = "20261018_1200_project_color"

_VERSION = "20261019_0900_runner_version"

_NEW = {"reported_version", "version_refusal", "version_judged_at"}

_AT = datetime(2026, 10, 19, tzinfo=UTC)


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


def test_the_upgrade_adds_the_columns_and_leaves_every_runner_unjudged(tmp_path: Path) -> None:
    runner, engine = migrate_to(tmp_path, _PARENT)
    _runner(engine, default_tenant(engine))

    runner.upgrade(_VERSION)

    columns = _columns(engine, "runner_registrations")
    assert all(columns[name] for name in _NEW)
    with engine.connect() as conn:
        row = conn.execute(
            sa.text("SELECT reported_version, version_refusal, version_judged_at FROM runner_registrations")
        ).one()
    assert tuple(row) == (None, None, None)


def test_the_new_columns_match_the_declared_schema(tmp_path: Path) -> None:
    _, engine = migrate_to(tmp_path, "head")
    declared = create_engine_from_url(f"sqlite:///{tmp_path / 'declared.db'}")
    try:
        s.metadata.create_all(declared)
        migrated, wanted = _columns(engine, "runner_registrations"), _columns(declared, "runner_registrations")
        assert {name: migrated[name] for name in _NEW} == {name: wanted[name] for name in _NEW}
    finally:
        declared.dispose()


def test_a_rerun_over_a_store_already_reshaped_changes_nothing(tmp_path: Path) -> None:
    runner, engine = migrate_to(tmp_path, _VERSION)
    schema = schema_snapshot(engine, exemptions={}, name_exemptions={})
    with engine.begin() as conn:
        conn.execute(sa.text(f"UPDATE alembic_version SET version_num = '{_PARENT}'"))

    runner.upgrade(_VERSION)

    assert schema_snapshot(engine, exemptions={}, name_exemptions={}) == schema


def test_the_downgrade_restores_the_store_exactly(tmp_path: Path) -> None:
    runner, engine = migrate_to(tmp_path, _PARENT)
    before = schema_snapshot(engine, exemptions={}, name_exemptions={})
    _runner(engine, default_tenant(engine))
    runner.upgrade(_VERSION)

    runner.downgrade(_PARENT)

    assert schema_snapshot(engine, exemptions={}, name_exemptions={}) == before
    assert not _NEW & set(_columns(engine, "runner_registrations"))
    with engine.connect() as conn:
        assert conn.execute(sa.text("SELECT runner_id FROM runner_registrations")).scalars().all() == ["rn_a"]
    runner.upgrade(_VERSION)
    assert set(_columns(engine, "runner_registrations")) >= _NEW

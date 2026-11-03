"""The permit-revision revision: the column is added as the schema declares it, every existing permit starts at
revision 1, a re-run changes nothing, and the downgrade restores the store exactly, its permits kept."""

# exemplar: bzh:manual-migrations. The revision sits outside the hub's chain, so ``_parent`` drives the permits
# revision over a store the real chain migrated to its parent; a production test calls ``runner.upgrade``.

from __future__ import annotations

import importlib
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

import pytest
import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations

from blizzard.foundation.store.engine import create_engine_from_url
from exemplar.hub.store import schema as s
from tests.migration_support_store_migrations import schema_snapshot
from tests.support import default_project, default_tenant, migrate_to

pytestmark = pytest.mark.component

_PERMITS = importlib.import_module("exemplar.hub.store.migrations.versions.20261015_0900_permits")
_REVISION = importlib.import_module("exemplar.hub.store.migrations.versions.20261103_0900_permit_revision")
_AT = datetime(2026, 11, 3, tzinfo=UTC)


def _apply(engine: sa.Engine, step: Callable[[], None]) -> None:
    with engine.begin() as conn, Operations.context(MigrationContext.configure(conn)):
        step()


def _parent(tmp_path: Path) -> sa.Engine:
    """A store at this revision's parent, holding one requested permit."""
    _, engine = migrate_to(tmp_path, _PERMITS.down_revision)
    _apply(engine, _PERMITS.upgrade)
    with engine.begin() as conn:
        conn.execute(
            sa.text(
                "INSERT INTO permits (permit_id, holder, capability, requested_at, requested_by, tenant_id, project_id)"
                " VALUES ('pm_a', 'ada', 'deploy', :at, 'op', :tenant, :project)"
            ),
            {"at": _AT, "tenant": default_tenant(engine), "project": default_project(engine)},
        )
    return engine


def _columns(engine: sa.Engine) -> dict[str, tuple[str, bool]]:
    return {c["name"]: (str(c["type"]), bool(c["nullable"])) for c in sa.inspect(engine).get_columns("permits")}


def _snapshot(engine: sa.Engine) -> dict[str, object]:
    return schema_snapshot(engine, exemptions={}, name_exemptions={})


def test_the_upgrade_starts_every_existing_permit_at_revision_one(tmp_path: Path) -> None:
    engine = _parent(tmp_path)
    assert "revision" not in _columns(engine)

    _apply(engine, _REVISION.upgrade)

    with engine.connect() as conn:
        assert conn.execute(sa.text("SELECT permit_id, revision FROM permits")).all() == [("pm_a", 1)]


def test_the_column_matches_the_declared_schema(tmp_path: Path) -> None:
    engine = _parent(tmp_path)
    _apply(engine, _REVISION.upgrade)
    declared = create_engine_from_url(f"sqlite:///{tmp_path / 'declared.db'}")
    try:
        s.metadata.create_all(declared)
        assert _columns(engine) == _columns(declared)
    finally:
        declared.dispose()


def test_a_rerun_over_a_store_already_reshaped_changes_nothing(tmp_path: Path) -> None:
    engine = _parent(tmp_path)
    _apply(engine, _REVISION.upgrade)
    schema = _snapshot(engine)

    _apply(engine, _REVISION.upgrade)

    assert _snapshot(engine) == schema


def test_the_downgrade_restores_the_store_exactly(tmp_path: Path) -> None:
    engine = _parent(tmp_path)
    before = _snapshot(engine)
    _apply(engine, _REVISION.upgrade)

    _apply(engine, _REVISION.downgrade)

    assert _snapshot(engine) == before
    with engine.connect() as conn:
        assert conn.execute(sa.text("SELECT permit_id FROM permits")).scalars().all() == ["pm_a"]
    _apply(engine, _REVISION.upgrade)
    assert "revision" in _columns(engine)

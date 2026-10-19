"""The project color revision: every project takes the palette entry at its rank among its tenant's projects, so each
tenant's first twelve are distinct; the column ends required; a run stopped after any step finishes as a single run
does; a re-run changes nothing; and the downgrade restores the store exactly."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest
import sqlalchemy as sa
from alembic.script import ScriptDirectory

from blizzard.foundation.clock import FixedClock
from blizzard.foundation.store.engine import create_engine_from_url
from blizzard.foundation.store.migrations import MigrationRunner
from blizzard.hub.config import HubConfig
from blizzard.hub.runtime import Migrations
from blizzard.hub.store import MIGRATIONS_DIR
from tests.migration_support_project_columns_migration import _run_step
from tests.migration_support_store_migrations import _schema_snapshot

pytestmark = [pytest.mark.component]

_PARENT = "20261018_0900_decision_check_results"

_REVISION = "20261018_1200_project_color"

_T0 = datetime(2026, 1, 1, tzinfo=UTC)

_NOW = datetime(2026, 10, 18, 12, 0, tzinfo=UTC)

_PALETTE = ("red", "orange", "amber", "lime", "green", "teal", "cyan", "blue", "indigo", "violet", "pink", "slate")

#: More projects than one fill page holds, so the busy tenant's fill crosses a page boundary.
_BUSY_PROJECTS = 1003

_QUIET = "ten_quiet"


def _revision_module() -> ModuleType:
    revision = ScriptDirectory(str(MIGRATIONS_DIR)).get_revision(_REVISION)
    assert revision is not None
    return revision.module


def _query(engine: sa.Engine, sql: str, **params: object) -> list[tuple[Any, ...]]:
    with engine.connect() as conn:
        return [tuple(row) for row in conn.execute(sa.text(sql), params)]


@contextmanager
def _at_the_parent(tmp_path: Path) -> Iterator[tuple[MigrationRunner, sa.Engine, str]]:
    """A store at the parent revision holding two tenants: the store's own, with its default project and enough more
    to cross a fill page, and a quiet one with three projects; the store's own tenant's id."""
    tmp_path.mkdir(parents=True, exist_ok=True)
    db_url = f"sqlite:///{tmp_path / 'hub.db'}"
    runner = Migrations(HubConfig(root=tmp_path, db_url=db_url), FixedClock(_NOW)).runner
    runner.upgrade(_PARENT)
    engine = create_engine_from_url(db_url)
    try:
        (busy,) = _query(engine, "SELECT tenant_id FROM tenants")[0]
        tenants = sa.Table("tenants", sa.MetaData(), autoload_with=engine)
        projects = sa.Table("projects", sa.MetaData(), autoload_with=engine)
        with engine.begin() as conn:
            conn.execute(
                sa.insert(tenants).values(tenant_id=_QUIET, name="quiet", created_by="migration", created_at=_T0)
            )
            seeded = [(busy, n) for n in range(_BUSY_PROJECTS)] + [(_QUIET, n) for n in range(3)]
            conn.execute(
                sa.insert(projects),
                [
                    {
                        "project_id": f"proj_{tenant}_{n:05d}",
                        "slug": f"p{n}",
                        "name": f"P{n}",
                        "description": "",
                        "revision": 1,
                        "created_at": _T0,
                        "tenant_id": tenant,
                    }
                    for tenant, n in seeded
                ],
            )
        yield runner, engine, busy
    finally:
        engine.dispose()


def _colors(engine: sa.Engine, tenant: str) -> list[str]:
    return [
        c for (c,) in _query(engine, "SELECT color FROM projects WHERE tenant_id = :t ORDER BY project_id", t=tenant)
    ]


def _expected(count: int) -> list[str]:
    return [_PALETTE[rank % len(_PALETTE)] for rank in range(count)]


def test_each_project_takes_the_palette_entry_at_its_rank_in_its_tenant(tmp_path: Path) -> None:
    with _at_the_parent(tmp_path) as (runner, engine, busy):
        runner.upgrade(_REVISION)

        busy_colors = _colors(engine, busy)
        assert busy_colors == _expected(len(busy_colors))
        assert len(busy_colors) > _BUSY_PROJECTS
        assert len(set(busy_colors[:12])) == 12
        assert _colors(engine, _QUIET) == ["red", "orange", "amber"]


def test_the_column_ends_required(tmp_path: Path) -> None:
    with _at_the_parent(tmp_path) as (runner, engine, _busy):
        runner.upgrade(_REVISION)

        columns = {c["name"]: c for c in sa.inspect(engine).get_columns("projects")}
        assert columns["color"]["nullable"] is False
        assert columns["color"]["default"] is None


@pytest.mark.parametrize("stopped_after", [1, 2])
def test_a_run_stopped_after_any_step_finishes_as_a_single_run_does(tmp_path: Path, stopped_after: int) -> None:
    with _at_the_parent(tmp_path / "single") as (runner, engine, busy):
        runner.upgrade(_REVISION)
        single = (
            _colors(engine, busy),
            _colors(engine, _QUIET),
            _schema_snapshot(engine, exemptions={}, name_exemptions={}),
        )
    with _at_the_parent(tmp_path / "stopped") as (runner, engine, busy):
        steps = _revision_module().UPGRADE_STEPS
        with engine.begin() as conn:
            for step in steps[:stopped_after]:
                _run_step(conn, step)

        runner.upgrade(_REVISION)

        assert (
            _colors(engine, busy),
            _colors(engine, _QUIET),
            _schema_snapshot(engine, exemptions={}, name_exemptions={}),
        ) == single


def test_a_re_run_changes_nothing(tmp_path: Path) -> None:
    with _at_the_parent(tmp_path) as (runner, engine, busy):
        runner.upgrade(_REVISION)
        snapshot = _schema_snapshot(engine, exemptions={}, name_exemptions={})
        colors = _colors(engine, busy)

        with engine.begin() as conn:
            for step in _revision_module().UPGRADE_STEPS:
                _run_step(conn, step)

        assert _schema_snapshot(engine, exemptions={}, name_exemptions={}) == snapshot
        assert _colors(engine, busy) == colors


def test_the_downgrade_restores_the_store_exactly(tmp_path: Path) -> None:
    with _at_the_parent(tmp_path) as (runner, engine, _busy):
        snapshot = _schema_snapshot(engine, exemptions={}, name_exemptions={})
        projects = _query(engine, "SELECT * FROM projects ORDER BY project_id")

        runner.upgrade(_REVISION)
        runner.downgrade(_PARENT)

        assert runner.current_revision() == _PARENT
        assert _schema_snapshot(engine, exemptions={}, name_exemptions={}) == snapshot
        assert _query(engine, "SELECT * FROM projects ORDER BY project_id") == projects

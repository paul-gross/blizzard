"""The project columns revision: every runner row comes to serve its tenant's one project, declared by the project's
id; a landed repository row gains a null record name; nothing else changes; the upgrade refuses while a tenant holding
a runner holds other than one project; a run stopped after any step finishes as a single run does; the fill pages in
bounded batches; and the downgrade restores the store exactly."""

from __future__ import annotations

import json
from collections.abc import Iterator
from contextlib import contextmanager
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
from tests.migration_support_project_columns_migration import _NOW, _T0, _insert, _query, _run_step
from tests.migration_support_store_migrations import (
    _schema_snapshot,
    assert_frozen_table_lacks_a_later_revisions_columns,
)

_CLOSED_AT = "20261008_1720_tenant_deletion_closed_at"

_READ_INDEXES = "20261008_1810_project_read_indexes"

_COLUMNS = "20261008_1900_project_columns"

_IDLE = "ten_idle"


def _revision_module() -> ModuleType:
    revision = ScriptDirectory(str(MIGRATIONS_DIR)).get_revision(_COLUMNS)
    assert revision is not None
    return revision.module


@contextmanager
def _at_the_read_indexes(tmp_path: Path, *, runners: int = 2) -> Iterator[tuple[MigrationRunner, sa.Engine, str]]:
    """A store at the read-indexes revision holding two tenants, each with its one project: the store's own, with
    ``runners`` runners and a landed repository row, and an idle one with neither; the store's own tenant's id."""
    db_url = f"sqlite:///{tmp_path / 'hub.db'}"
    runner = Migrations(HubConfig(root=tmp_path, db_url=db_url), FixedClock(_NOW)).runner
    runner.upgrade(_CLOSED_AT)
    engine = create_engine_from_url(db_url)
    try:
        _insert(engine, "tenants", [{"tenant_id": _IDLE, "name": "idle", "created_by": "migration"}])
        (tenant,) = _query(engine, "SELECT tenant_id FROM tenants WHERE tenant_id != :i", i=_IDLE)[0]
        t = {"tenant_id": tenant}
        _insert(engine, "chunks", [{**t, "chunk_id": "ch_00000", "minted_at": _T0}])
        _insert(
            engine,
            "delivery_repo_landed",
            [{**t, "chunk_id": "ch_00000", "repo": "acme/widget", "commit_hash": "abc123", "landed_at": _T0}],
        )
        _insert(
            engine,
            "runner_registrations",
            [{**t, "runner_id": f"run_{n:05d}", "name": f"r{n}", "added_at": _T0} for n in range(runners)],
        )
        runner.upgrade(_READ_INDEXES)
        yield runner, engine, tenant
    finally:
        engine.dispose()


def _project_of(engine: sa.Engine, tenant: str) -> str:
    return _query(engine, "SELECT project_id FROM projects WHERE tenant_id = :t", t=tenant)[0][0]


def _served(engine: sa.Engine) -> dict[str, Any]:
    rows = _query(engine, "SELECT runner_id, projects FROM runner_registrations ORDER BY runner_id")
    return {runner_id: None if served is None else json.loads(served) for runner_id, served in rows}


def _rows(engine: sa.Engine, table: str) -> list[tuple[Any, ...]]:
    return sorted(_query(engine, f"SELECT * FROM {table}"), key=repr)


def _all_rows(engine: sa.Engine) -> dict[str, list[tuple[Any, ...]]]:
    return {table: _rows(engine, table) for table in sorted(sa.inspect(engine).get_table_names())}


def _labelled(value: object, labels: dict[str, str]) -> object:
    if not isinstance(value, str):
        return value
    for minted, label in labels.items():
        value = value.replace(minted, label)
    return value


def _shape(engine: sa.Engine) -> tuple[object, ...]:
    """The store with every minted tenant and project id read as a label, wherever it appears: its schema and every
    row."""
    minted = _query(engine, "SELECT tenant_id, project_id FROM projects ORDER BY tenant_id = :i", i=_IDLE)
    labels = {
        id_: f"{kind}-{n}" for n, row in enumerate(minted) for kind, id_ in zip(("tenant", "project"), row, strict=True)
    }
    rows = {
        table: sorted((tuple(_labelled(v, labels) for v in row) for row in values), key=repr)
        for table, values in _all_rows(engine).items()
    }
    return _schema_snapshot(engine, exemptions={}, name_exemptions={}), rows


@pytest.mark.component
def test_every_runner_serves_its_tenants_one_project_and_landed_rows_name_no_record(tmp_path: Path) -> None:
    with _at_the_read_indexes(tmp_path) as (runner, engine, tenant):
        landed = _rows(engine, "delivery_repo_landed")

        runner.upgrade(_COLUMNS)

        project = _project_of(engine, tenant)
        assert _served(engine) == {
            "run_00000": [{"entry": project, "project_id": project}],
            "run_00001": [{"entry": project, "project_id": project}],
        }
        assert _rows(engine, "delivery_repo_landed") == [(*row, None) for row in landed]


@pytest.mark.component
def test_nothing_but_the_two_columns_changes(tmp_path: Path) -> None:
    with _at_the_read_indexes(tmp_path) as (runner, engine, _tenant):
        before = _all_rows(engine)

        runner.upgrade(_COLUMNS)

        after = _all_rows(engine)
        kept = set(before) - {"alembic_version", "runner_registrations", "delivery_repo_landed"}
        assert {t: after[t] for t in kept} == {t: before[t] for t in kept}
        assert set(after) == set(before)
        assert [row[:-1] for row in _rows(engine, "runner_registrations")] == before["runner_registrations"]


@pytest.mark.component
def test_a_runner_already_declaring_its_projects_keeps_its_declaration(tmp_path: Path) -> None:
    with _at_the_read_indexes(tmp_path) as (runner, engine, _tenant):
        module = _revision_module()
        with engine.begin() as conn:
            for step in module.UPGRADE_STEPS[:1]:
                _run_step(conn, step)
            conn.execute(sa.text("UPDATE runner_registrations SET projects = '[]' WHERE runner_id = 'run_00001'"))

        runner.upgrade(_COLUMNS)

        assert _served(engine)["run_00001"] == []


@pytest.mark.component
def test_the_upgrade_refuses_while_a_tenant_holding_a_runner_holds_two_projects(tmp_path: Path) -> None:
    with _at_the_read_indexes(tmp_path) as (runner, engine, tenant):
        _insert(engine, "projects", [{"project_id": "proj_second", "tenant_id": tenant, "slug": "second"}])
        _insert(engine, "projects", [{"project_id": "proj_idle_second", "tenant_id": _IDLE, "slug": "second"}])

        with pytest.raises(RuntimeError, match=tenant) as refused:
            runner.upgrade(_COLUMNS)

        assert _IDLE not in str(refused.value)
        assert runner.current_revision() == _READ_INDEXES


@pytest.mark.component
@pytest.mark.parametrize("steps", range(1, 3))
def test_a_run_stopped_after_any_step_finishes_as_a_single_run_does(tmp_path: Path, steps: int) -> None:
    (tmp_path / "stopped").mkdir()
    (tmp_path / "reference").mkdir()
    with _at_the_read_indexes(tmp_path / "stopped") as (runner, engine, _tenant):
        with engine.begin() as conn:
            for step in _revision_module().UPGRADE_STEPS[:steps]:
                _run_step(conn, step)

        runner.upgrade(_COLUMNS)

        assert runner.current_revision() == _COLUMNS
        with _at_the_read_indexes(tmp_path / "reference") as (reference_runner, reference, _):
            reference_runner.upgrade(_COLUMNS)
            assert _shape(engine) == _shape(reference)


@pytest.mark.component
def test_the_fill_pages_in_bounded_batches_and_misses_no_runner(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(_revision_module(), "_BATCH_ROWS", 2)
    with _at_the_read_indexes(tmp_path, runners=7) as (runner, engine, tenant):
        runner.upgrade(_COLUMNS)

        project = _project_of(engine, tenant)
        assert list(_served(engine).values()) == [[{"entry": project, "project_id": project}]] * 7


@pytest.mark.component
def test_the_downgrade_drops_both_columns_and_restores_the_store_exactly(tmp_path: Path) -> None:
    with _at_the_read_indexes(tmp_path) as (runner, engine, _tenant):
        shape = _shape(engine)

        runner.upgrade(_COLUMNS)
        runner.downgrade(_READ_INDEXES)

        assert runner.current_revision() == _READ_INDEXES
        assert _shape(engine) == shape


@pytest.mark.unit
@pytest.mark.parametrize(
    "tree,parent_revision,table,columns,direction",
    [
        pytest.param(
            "hub",
            "20261008_1810_project_read_indexes",
            "runner_registrations",
            ("projects",),
            "added",
            id="hub.runner_registrations.projects",
        ),
    ],
)
def test_frozen_table_lacks_a_later_revisions_columns(
    tree: str, parent_revision: str, table: str, columns: tuple[str, ...], direction: str, tmp_path: Path
) -> None:
    assert_frozen_table_lacks_a_later_revisions_columns(tree, parent_revision, table, columns, direction, tmp_path)

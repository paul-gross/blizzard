"""The urgencies revision creates the four tables and ``chunks.urgency_id`` as ``src/blizzard/hub/store/schema.py``
declares them, gives every tenant one ``standard`` at position ``0`` and settings naming it, each with its ``create``
change row from the ``migration`` door, and gives every chunk — at every status — its tenant's ``standard``; a tenant
with no chunk gets its own ``standard`` too. Ids are the same on every run, a run stopped after any step finishes as a
single run does, the fill pages in bounded batches, and the downgrade restores the parent store.

Seeded from the store's reflected shape at the parent revision rather than the live schema."""

from __future__ import annotations

import re
from collections.abc import Generator
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
from blizzard.hub.store import schema as s
from tests.support.migrations.project_columns_migration import NOW, insert, query, run_step
from tests.support.migrations.store_migrations import schema_snapshot

pytestmark = [pytest.mark.component]

_PARENT = "20261106_0900_errand_marks"
_REVISION = "20261108_0900_urgencies"
_SECOND = "ten_second"
_EMPTY = "ten_empty"
_URGENCY_ID = re.compile(r"^urg_[0-9A-HJKMNP-TV-Z]{26}$")
_NEW_TABLES = ("urgencies", "urgency_lifecycle_facts", "urgency_positions", "tenant_settings")


def _module() -> ModuleType:
    found = ScriptDirectory(str(MIGRATIONS_DIR)).get_revision(_REVISION)
    assert found is not None
    return found.module


def _chunks(engine: sa.Engine, tenant: str, project: str, prefix: str, count: int) -> None:
    """``count`` chunks of ``tenant``; every third stopped, every third completed, the rest open."""
    insert(
        engine,
        "chunks",
        [
            {"chunk_id": f"ch_{prefix}_{n:05d}", "tenant_id": tenant, "project_id": project, "graph_id": "gr_x"}
            for n in range(count)
        ],
    )
    insert(
        engine,
        "chunk_stopped",
        [{"chunk_id": f"ch_{prefix}_{n:05d}", "tenant_id": tenant} for n in range(0, count, 3)],
    )
    insert(
        engine,
        "chunk_completed",
        [{"chunk_id": f"ch_{prefix}_{n:05d}", "tenant_id": tenant} for n in range(1, count, 3)],
    )


@contextmanager
def _before(tmp_path: Path, *, chunks: int = 9) -> Generator[tuple[MigrationRunner, sa.Engine, str]]:
    """A store at the parent revision: its own tenant holds ``chunks`` chunks at mixed statuses, a second tenant holds
    two, and a third holds none. The runner, the engine, and the store's own tenant id."""
    db_url = f"sqlite:///{tmp_path / 'hub.db'}"
    runner = Migrations(HubConfig(root=tmp_path, db_url=db_url), FixedClock(NOW)).runner
    runner.upgrade(_PARENT)
    engine = create_engine_from_url(db_url)
    try:
        insert(
            engine,
            "tenants",
            [
                {"tenant_id": _SECOND, "name": "second", "created_by": "migration"},
                {"tenant_id": _EMPTY, "name": "empty", "created_by": "migration"},
            ],
        )
        (tenant,) = query(engine, "SELECT tenant_id FROM tenants WHERE tenant_id NOT IN (:a, :b)", a=_SECOND, b=_EMPTY)[
            0
        ]
        (project,) = query(engine, "SELECT project_id FROM projects WHERE tenant_id = :t", t=tenant)[0]
        insert(engine, "projects", [{"project_id": "proj_second", "tenant_id": _SECOND, "slug": "p", "name": "p"}])
        _chunks(engine, tenant, project, "own", chunks)
        _chunks(engine, _SECOND, "proj_second", "two", 2)
        yield runner, engine, tenant
    finally:
        engine.dispose()


def _standards(engine: sa.Engine) -> dict[str, tuple[Any, ...]]:
    rows = query(
        engine,
        "SELECT tenant_id, urgency_id, slug, name, color, emoji, icon_digest, created_by, revision FROM urgencies",
    )
    return {row[0]: row[1:] for row in rows}


def _labels(engine: sa.Engine) -> dict[str, str]:
    """The store's own tenant id, its project's, and every minted urgency id, each read as a label."""
    (tenant,) = query(engine, "SELECT tenant_id FROM tenants WHERE tenant_id NOT IN (:a, :b)", a=_SECOND, b=_EMPTY)[0]
    (project,) = query(engine, "SELECT project_id FROM projects WHERE tenant_id = :t", t=tenant)[0]
    labels = {tenant: "tenant", project: "project"}
    if sa.inspect(engine).has_table("urgencies"):
        for tenant_id, urgency_id in query(engine, "SELECT tenant_id, urgency_id FROM urgencies"):
            labels[urgency_id] = f"urgency-{labels.get(tenant_id, tenant_id)}"
    return labels


def _labelled(text: str, labels: dict[str, str]) -> str:
    for minted, label in labels.items():
        text = text.replace(minted, label)
    return text


def _shape(engine: sa.Engine) -> tuple[object, ...]:
    """The schema and every row of the touched tables, a change row's id aside, minted ids read as labels."""
    labels = _labels(engine)
    rows = {
        table: sorted(_labelled(repr(row), labels) for row in query(engine, f"SELECT * FROM {table}"))
        for table in (*_NEW_TABLES, "chunks")
        if sa.inspect(engine).has_table(table)
    }
    changes = sorted(
        _labelled(repr(row), labels)
        for row in query(
            engine, "SELECT record_kind, record_key, revision, op, diff, door, actor, project_id FROM config_changes"
        )
    )
    return schema_snapshot(engine, exemptions={}, name_exemptions={}), rows, changes


def test_the_upgrade_shapes_the_store_as_the_schema_declares(tmp_path: Path) -> None:
    with _before(tmp_path) as (runner, engine, _):
        runner.upgrade(_REVISION)
        declared = create_engine_from_url(f"sqlite:///{tmp_path / 'declared.db'}")
        try:
            s.metadata.create_all(declared)
            migrated, wanted = (schema_snapshot(e, exemptions={}, name_exemptions={}) for e in (engine, declared))
            assert {t: migrated[t] for t in (*_NEW_TABLES, "chunks")} == {
                t: wanted[t] for t in (*_NEW_TABLES, "chunks")
            }
        finally:
            declared.dispose()


def test_every_tenant_gets_one_standard_first_in_its_order_and_settings_naming_it(tmp_path: Path) -> None:
    with _before(tmp_path) as (runner, engine, tenant):
        runner.upgrade(_REVISION)

        standards = _standards(engine)
        assert set(standards) == {tenant, _SECOND, _EMPTY}
        for tenant_id, (urgency_id, *rest) in standards.items():
            assert _URGENCY_ID.match(urgency_id)
            assert rest == ["standard", "standard", "slate", None, None, "migration", 1]
            assert query(
                engine, "SELECT position, set_by FROM urgency_positions WHERE urgency_id = :u", u=urgency_id
            ) == [(0.0, "migration")]
            assert query(
                engine,
                "SELECT default_urgency_id, revision, updated_by FROM tenant_settings WHERE tenant_id = :t",
                t=tenant_id,
            ) == [(urgency_id, 1, "migration")]
        assert len({u for u, *_ in standards.values()}) == 3
        assert query(engine, "SELECT count(*) FROM urgency_lifecycle_facts") == [(0,)]


def test_each_seeded_record_has_its_create_change_row_from_the_migration_door(tmp_path: Path) -> None:
    with _before(tmp_path) as (runner, engine, tenant):
        runner.upgrade(_REVISION)

        urgency_id = _standards(engine)[tenant][0]
        rows = query(
            engine,
            "SELECT record_kind, record_key, revision, op, door, actor, project_id, diff FROM config_changes "
            "WHERE tenant_id = :t AND record_kind IN ('urgency', 'tenant_settings') ORDER BY record_kind",
            t=tenant,
        )
        assert rows == [
            (
                "tenant_settings",
                tenant,
                1,
                "create",
                "migration",
                "migration",
                None,
                '[{"field": "default_urgency", "old": null, "new": "standard"}]',
            ),
            (
                "urgency",
                urgency_id,
                1,
                "create",
                "migration",
                "migration",
                None,
                '[{"field": "slug", "old": null, "new": "standard"}, {"field": "name", "old": null, "new": "standard"}, '
                '{"field": "color", "old": null, "new": "slate"}]',
            ),
        ]


def test_every_chunk_at_every_status_carries_its_tenants_standard(tmp_path: Path) -> None:
    with _before(tmp_path) as (runner, engine, _):
        runner.upgrade(_REVISION)

        standards = _standards(engine)
        rows = query(engine, "SELECT tenant_id, urgency_id FROM chunks")
        assert len(rows) == 11
        assert all(urgency_id == standards[tenant_id][0] for tenant_id, urgency_id in rows)


def test_ids_are_the_same_on_every_run(tmp_path: Path) -> None:
    with _before(tmp_path) as (runner, engine, _):
        runner.upgrade(_REVISION)
        first = _standards(engine)
        runner.downgrade(_PARENT)
        runner.upgrade(_REVISION)
        assert _standards(engine) == first


def test_a_rerun_over_a_carried_over_store_writes_nothing(tmp_path: Path) -> None:
    with _before(tmp_path) as (runner, engine, _):
        runner.upgrade(_REVISION)
        shape = _shape(engine)
        with engine.begin() as conn:
            conn.execute(sa.text(f"UPDATE alembic_version SET version_num = '{_PARENT}'"))

        runner.upgrade(_REVISION)

        assert _shape(engine) == shape


@pytest.mark.parametrize("steps", range(1, 6))
def test_a_run_stopped_after_any_step_finishes_as_a_single_run_does(tmp_path: Path, steps: int) -> None:
    (tmp_path / "stopped").mkdir()
    (tmp_path / "reference").mkdir()
    with _before(tmp_path / "stopped") as (runner, engine, _):
        with engine.begin() as conn:
            for step in _module().UPGRADE_STEPS[:steps]:
                run_step(conn, step)

        runner.upgrade(_REVISION)

        assert runner.current_revision() == _REVISION
        with _before(tmp_path / "reference") as (reference_runner, reference, _):
            reference_runner.upgrade(_REVISION)
            assert _shape(engine) == _shape(reference)


def test_the_fill_pages_in_bounded_batches_and_misses_no_row(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(_module(), "_BATCH_ROWS", 2)
    with _before(tmp_path, chunks=7) as (runner, engine, _):
        runner.upgrade(_REVISION)

        assert query(engine, "SELECT count(*) FROM chunks WHERE urgency_id IS NULL") == [(0,)]


def test_the_downgrade_restores_the_parent_store_and_the_upgrade_runs_again(tmp_path: Path) -> None:
    with _before(tmp_path) as (runner, engine, _):
        before = schema_snapshot(engine, exemptions={}, name_exemptions={})
        changes = query(engine, "SELECT count(*) FROM config_changes")
        runner.upgrade(_REVISION)
        after = _shape(engine)

        runner.downgrade(_PARENT)

        assert schema_snapshot(engine, exemptions={}, name_exemptions={}) == before
        assert query(engine, "SELECT count(*) FROM config_changes") == changes
        assert query(engine, "SELECT count(*) FROM chunks") == [(11,)]
        runner.upgrade(_REVISION)
        assert _shape(engine)[0] == after[0]

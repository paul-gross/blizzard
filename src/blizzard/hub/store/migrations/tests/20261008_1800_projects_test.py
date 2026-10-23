"""Component coverage for the facts-level invariant checker (``bzh:invariant-checker``).

Migrates real hub + runner stores, asserts a clean store yields no violations, then
injects each kind of corruption and asserts the matching invariant is named. Every
corruption is injected on a head-migrated store, the only shape the checker is
contracted to read."""

from __future__ import annotations

import json
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest
import sqlalchemy as sa
from alembic.config import Config
from alembic.operations import Operations
from alembic.runtime.environment import EnvironmentContext
from alembic.runtime.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy.engine.interfaces import ReflectedColumn
from sqlalchemy.exc import IntegrityError

from blizzard.foundation.clock import FixedClock
from blizzard.foundation.store.engine import create_engine_from_url
from blizzard.foundation.store.migrations import MIGRATION_CLOCK_ATTRIBUTE, MigrationRunner
from blizzard.hub.config import HubConfig
from blizzard.hub.runtime import Migrations
from blizzard.hub.store import MIGRATIONS_DIR
from blizzard.hub.store import schema as hub
from blizzard.tools.invariants import HubInvariants
from tests.migration_support_invariant_checker import _NOW as _INVARIANT_NOW
from tests.migration_support_invariant_checker import _PROJECT_INVARIANTS, insert
from tests.migration_support_store_migrations import (
    _schema_snapshot,
    assert_frozen_table_lacks_a_later_revisions_columns,
)
from tests.support import (
    MIGRATION_CLOCK,
    hub_scoped_engine,
    migrate_to,
)

_CLOSED_AT = "20261008_1720_tenant_deletion_closed_at"

_PROJECTS = "20261008_1800_projects"

_T0 = datetime(2026, 1, 1, tzinfo=UTC)

_NOW = datetime(2026, 10, 8, 18, 0, tzinfo=UTC)

_EMPTY = "ten_empty"

_OWNED = (
    "chunks",
    "scopes",
    "routines",
    "findings",
    "finding_sets",
    "garden_proposals",
    "work_item_runs",
    "work_items",
)

_UNTOUCHED = (
    "chunk_dependencies",
    "work_item_sequence",
    "scope_lifecycle_facts",
    "routine_lifecycle_facts",
    "work_source_lifecycle_facts",
    "repository_lifecycle_facts",
    "finding_facts",
)


def _revision_module() -> ModuleType:
    revision = ScriptDirectory(str(MIGRATIONS_DIR)).get_revision(_PROJECTS)
    assert revision is not None
    return revision.module


@contextmanager
def _at_the_closed_at(tmp_path: Path) -> Iterator[tuple[MigrationRunner, sa.Engine]]:
    db_url = f"sqlite:///{tmp_path / 'hub.db'}"
    runner = Migrations(HubConfig(root=tmp_path, db_url=db_url), FixedClock(_NOW)).runner
    runner.upgrade(_CLOSED_AT)
    engine = create_engine_from_url(db_url)
    try:
        yield runner, engine
    finally:
        engine.dispose()


def _filler(column: ReflectedColumn) -> object:
    kind = str(column["type"]).upper()
    if "INT" in kind:
        return 1
    if "BOOL" in kind:
        return False
    if "DATE" in kind or "TIME" in kind:
        return _T0
    return "x"


def _insert(engine: sa.Engine, table: str, rows: list[dict[str, object]]) -> None:
    """``rows`` into ``table`` as the store holds it, each required column the row leaves out filled."""
    columns = sa.inspect(engine).get_columns(table)
    required = [c for c in columns if not c["nullable"] and c["default"] is None and not c.get("autoincrement")]
    reflected = sa.Table(table, sa.MetaData(), autoload_with=engine)
    with engine.begin() as conn:
        for row in rows:
            filled = {c["name"]: _filler(c) for c in required if c["name"] not in row}
            if "id" in filled and reflected.c.id.primary_key and isinstance(reflected.c.id.type, sa.Integer):
                del filled["id"]
            conn.execute(sa.insert(reflected).values({**filled, **row}))


def _tenant(engine: sa.Engine) -> str:
    with engine.connect() as conn:
        return conn.execute(sa.text("SELECT tenant_id FROM tenants WHERE tenant_id != :e"), {"e": _EMPTY}).scalar_one()


def _seed(engine: sa.Engine, *, chunks: int = 2) -> str:
    """A second, empty tenant beside the store's own, which holds rows in every project-owned table, a work source, a
    repository, their facts and change rows; the store's own tenant's id."""
    _insert(engine, "tenants", [{"tenant_id": _EMPTY, "name": "empty", "created_by": "migration"}])
    tenant = _tenant(engine)
    t = {"tenant_id": tenant}
    _insert(
        engine,
        "chunks",
        [{**t, "chunk_id": f"ch_{n:05d}", "minted_at": _T0 + timedelta(days=1 + n)} for n in range(chunks)],
    )
    _insert(
        engine,
        "chunk_dependencies",
        [{**t, "dependency_id": "dep_a", "dependent_chunk_id": "ch_00000", "prerequisite_chunk_id": "ch_00001"}],
    )
    _insert(engine, "scopes", [{**t, "scope_id": "scp_a", "slug": "repo", "description": ""}])
    _insert(engine, "scope_lifecycle_facts", [{**t, "scope_id": "scp_a", "retired": False, "set_by": "op"}])
    _insert(engine, "routines", [{**t, "routine_id": "rtn_a", "name": "nightly", "default_scope_id": "scp_a"}])
    _insert(engine, "routine_lifecycle_facts", [{**t, "routine_id": "rtn_a", "retired": False}])
    _insert(engine, "findings", [{**t, "finding_id": "fin_a", "scope_id": "scp_a", "source": "routine"}])
    _insert(engine, "finding_facts", [{**t, "finding_id": "fin_a", "kind": "add"}])
    _insert(engine, "finding_sets", [{**t, "finding_set_id": "fins_a", "artifact_id": "a1", "scope_id": "scp_a"}])
    _insert(
        engine,
        "garden_proposals",
        [{**t, "proposal_id": "gprop_a", "origin": "routine-run", "routine_name": "nightly"}],
    )
    _insert(engine, "work_items", [{**t, "work_item_id": "wi_a", "source": "hub", "ref": "1"}])
    _insert(engine, "work_item_runs", [{**t, "work_item_id": "wi_a", "scope_id": "scp_a"}])
    _insert(engine, "work_item_sequence", [{**t, "source": "hub", "next_ref": 2}])
    _insert(engine, "work_sources", [{**t, "name": "github", "annotate": False}])
    _insert(engine, "work_source_lifecycle_facts", [{**t, "name": "github", "retired": False}])
    _insert(engine, "repositories", [{**t, "name": "blizzard"}, {**t, "name": "mock", "repo": "mock"}])
    _insert(engine, "repository_lifecycle_facts", [{**t, "name": "blizzard", "retired": False}])
    changes = [("scope", "repo"), ("routine", "nightly"), ("work_source", "github"), ("repository", "blizzard")]
    _insert(
        engine,
        "config_changes",
        [
            {**t, "record_kind": kind, "record_key": key, "door": "api", "op": "create", "diff": "[]"}
            for kind, key in changes
        ],
    )
    return tenant


def _rows(engine: sa.Engine, table: str) -> list[tuple[object, ...]]:
    with engine.connect() as conn:
        return sorted(tuple(row) for row in conn.execute(sa.text(f"SELECT * FROM {table}")))


def _all_rows(engine: sa.Engine) -> dict[str, list[tuple[object, ...]]]:
    names = [n for n in sa.inspect(engine).get_table_names() if n != "alembic_version"]
    return {name: _rows(engine, name) for name in names}


def _query(engine: sa.Engine, sql: str, **params: object) -> list[tuple[Any, ...]]:
    with engine.connect() as conn:
        return [tuple(row) for row in conn.execute(sa.text(sql), params)]


def _projects(engine: sa.Engine) -> dict[str, tuple[Any, ...]]:
    rows = _query(engine, "SELECT tenant_id, project_id, slug, name, description, revision FROM projects")
    assert len({row[0] for row in rows}) == len(rows), "a tenant holds more than one project"
    return {row[0]: row[1:] for row in rows}


def _stopped_after(engine: sa.Engine, steps: int) -> None:
    module = _revision_module()
    config = Config()
    config.attributes[MIGRATION_CLOCK_ATTRIBUTE] = FixedClock(_NOW)
    environment = EnvironmentContext(config, ScriptDirectory(str(MIGRATIONS_DIR)))
    with engine.begin() as conn, Operations.context(MigrationContext.configure(conn, environment_context=environment)):
        for step in module.UPGRADE_STEPS[:steps]:
            step(conn)


def _shape(engine: sa.Engine) -> tuple[object, ...]:
    """The store with every minted id read as a label — a tenant as ``own`` or ``empty``, a project as its tenant's
    label: its schema and every row."""
    labels = {_tenant(engine): "own", _EMPTY: "empty"}
    projects = {
        **labels,
        **{project_id: f"{labels[tenant]}-project" for tenant, (project_id, *_) in _projects(engine).items()},
    }
    rows = {
        table: sorted(tuple(projects.get(v, v) if isinstance(v, str) else v for v in row) for row in values)
        for table, values in _all_rows(engine).items()
    }
    return _schema_snapshot(engine, exemptions={}, name_exemptions={}), rows


@pytest.mark.component
def test_a_carried_over_store_keeps_every_project_invariant(tmp_path: Path) -> None:
    """Rows written before projects existed, in two tenants, each land in their own tenant's ``default`` project. A
    carried chunk was minted before the migration created that project, and a carried landing names no repository."""
    runner, engine = migrate_to(tmp_path, "20261008_1720_tenant_deletion_closed_at")
    carried_mint = MIGRATION_CLOCK.now() - timedelta(days=1)
    seeding = hub_scoped_engine(engine)
    with seeding.begin() as conn:
        first = conn.execute(sa.select(hub.tenants.c.tenant_id)).scalar_one()
        conn.execute(
            insert(hub.tenants).values(
                tenant_id="tnt_second", name="second", created_at=_INVARIANT_NOW, created_by="test"
            )
        )
        for n, tenant in enumerate((first, "tnt_second")):
            scope, routine, chunk, finding = f"scp_{n}", f"rtn_{n}", f"ch_{n}", f"fin_{n}"
            conn.execute(
                insert(hub.scopes).values(
                    tenant_id=tenant, scope_id=scope, slug="s", description="", created_at=_INVARIANT_NOW
                )
            )
            conn.execute(
                insert(hub.routines).values(
                    tenant_id=tenant,
                    routine_id=routine,
                    name="r",
                    graph_name="g",
                    default_scope_id=scope,
                    created_at=_INVARIANT_NOW,
                )
            )
            conn.execute(insert(hub.routine_scopes).values(tenant_id=tenant, routine_id=routine, scope_id=scope))
            conn.execute(
                insert(hub.chunks).values(
                    tenant_id=tenant, chunk_id=chunk, graph_id="gr_1", minted_at=carried_mint, model="m"
                )
            )
            # The store here predates ``work_source_id``, which the current model's table already carries.
            conn.execute(
                insert(sa.Table("work_sources", sa.MetaData(), autoload_with=conn)).values(
                    tenant_id=tenant,
                    name="acme",
                    provider="github",
                    locator="acme/app",
                    annotate=False,
                    revision=1,
                    created_at=_INVARIANT_NOW,
                    created_by="test",
                )
            )
            # A stored source, a source never stored as a record, and the built-in hub source.
            for source in ("acme", "default", "hub"):
                conn.execute(
                    insert(hub.chunk_work_refs).values(tenant_id=tenant, chunk_id=chunk, source=source, ref="1")
                )
            conn.execute(
                insert(hub.delivery_repo_landed).values(
                    tenant_id=tenant, chunk_id=chunk, repo="acme/app", commit_hash="c0ffee", landed_at=_INVARIANT_NOW
                )
            )
            conn.execute(
                insert(hub.findings).values(
                    tenant_id=tenant,
                    finding_id=finding,
                    scope_id=scope,
                    scope_slug="s",
                    class_="c",
                    locus="a.py",
                    summary="s",
                    source="review",
                    raised_by_chunk_id=chunk,
                )
            )
            conn.execute(
                insert(hub.garden_proposals).values(
                    tenant_id=tenant,
                    proposal_id=f"gprop_{n}",
                    routine_name="r",
                    class_="c",
                    title="t",
                    body="",
                    created_at=_INVARIANT_NOW,
                )
            )
            conn.execute(
                insert(hub.garden_proposal_findings).values(
                    tenant_id=tenant, proposal_id=f"gprop_{n}", finding_id=finding
                )
            )
            conn.execute(
                insert(hub.work_items).values(
                    tenant_id=tenant,
                    work_item_id=f"wi_{n}",
                    source="hub",
                    ref="1",
                    title="t",
                    body="",
                    author_kind="fleet",
                    author_payload="{}",
                    created_at=_INVARIANT_NOW,
                    edited_at=_INVARIANT_NOW,
                )
            )
    runner.upgrade("head")
    try:
        violations = HubInvariants(engine).run()
        stranded = [v for v in violations if v.invariant == "hub:work-source-references-in-tenant"]
        assert {v.invariant for v in violations} & _PROJECT_INVARIANTS == {"hub:work-source-references-in-tenant"}
        # Only the ref naming a source never stored as a record is left as stored, and reported.
        assert [v.detail.split(" names ")[1] for v in stranded] == [
            "work source default, not a work source of its tenant"
        ] * 2
    finally:
        engine.dispose()


@pytest.mark.component
def test_every_tenant_holds_one_default_project_and_every_record_is_linked_to_it(tmp_path: Path) -> None:
    with _at_the_closed_at(tmp_path) as (runner, engine):
        tenant = _seed(engine)

        runner.upgrade(_PROJECTS)

        projects = _projects(engine)
        assert set(projects) == {tenant, _EMPTY}
        for project_id, *rest in projects.values():
            assert project_id.startswith("proj_")
            assert rest == ["default", "Default", "", 1]
        project = projects[tenant][0]
        created = _query(
            engine,
            "SELECT tenant_id, record_key, project_id, actor, door, op, revision FROM config_changes"
            " WHERE record_kind = 'project'",
        )
        assert sorted(created) == sorted(
            (t, p[0], p[0], "migration", "migration", "create", 1) for t, p in projects.items()
        )
        assert _query(engine, "SELECT tenant_id, project_id, source_name, revision FROM project_source_links") == [
            (tenant, project, "github", 1)
        ]
        assert sorted(
            _query(engine, "SELECT tenant_id, project_id, repository_name, revision FROM project_repository_links")
        ) == [(tenant, project, "blizzard", 1), (tenant, project, "mock", 1)]
        earliest = (_T0 + timedelta(days=1)).isoformat(sep=" ").removesuffix("+00:00")
        for facts, column in (
            ("project_source_link_facts", "source_name"),
            ("project_repository_link_facts", "repository_name"),
        ):
            for row in _query(engine, f"SELECT linked, set_by, set_at FROM {facts}"):
                assert row[:2] == (1, "migration")
                assert str(row[2]).startswith(earliest)
            assert _query(engine, f"SELECT count(*) FROM {facts} GROUP BY {column} HAVING count(*) != 1") == []
        links = _query(
            engine,
            "SELECT record_kind, record_key, project_id, door FROM config_changes"
            " WHERE record_kind IN ('project_source_link', 'project_repository_link')",
        )
        assert sorted(links) == [
            ("project_repository_link", "blizzard", project, "migration"),
            ("project_repository_link", "mock", project, "migration"),
            ("project_source_link", "github", project, "migration"),
        ]
        (project_diff,) = _query(
            engine, "SELECT diff FROM config_changes WHERE record_kind = 'project' AND tenant_id = :t", t=tenant
        )
        assert json.loads(project_diff[0]) == [
            {"field": "slug", "old": None, "new": "default"},
            {"field": "name", "old": None, "new": "Default"},
            {"field": "description", "old": None, "new": ""},
        ]
        (link_diff,) = _query(
            engine,
            "SELECT diff FROM config_changes WHERE record_kind = 'project_source_link' AND record_key = 'github'",
        )
        assert json.loads(link_diff[0]) == [{"field": "linked", "old": None, "new": True}]


@pytest.mark.component
def test_every_project_owned_row_and_only_scope_and_routine_change_rows_name_the_project(tmp_path: Path) -> None:
    with _at_the_closed_at(tmp_path) as (runner, engine):
        tenant = _seed(engine)

        runner.upgrade(_PROJECTS)

        project = _projects(engine)[tenant][0]
        for table in _OWNED:
            assert _query(engine, f"SELECT DISTINCT project_id FROM {table}") == [(project,)], table
        stamped = _query(
            engine,
            "SELECT record_kind, project_id FROM config_changes WHERE record_kind IN"
            " ('scope', 'routine', 'work_source', 'repository')",
        )
        assert sorted(stamped) == [
            ("repository", None),
            ("routine", project),
            ("scope", project),
            ("work_source", None),
        ]


@pytest.mark.component
def test_the_untouched_tables_keep_every_row(tmp_path: Path) -> None:
    with _at_the_closed_at(tmp_path) as (runner, engine):
        _seed(engine)
        before = {table: _rows(engine, table) for table in _UNTOUCHED}

        runner.upgrade(_PROJECTS)

        assert {table: _rows(engine, table) for table in _UNTOUCHED} == before
        assert all(before.values())


@pytest.mark.component
def test_a_slug_and_a_routine_name_are_unique_within_the_project_under_the_new_keys(tmp_path: Path) -> None:
    with _at_the_closed_at(tmp_path) as (runner, engine):
        _seed(engine)

        runner.upgrade(_PROJECTS)

        inspector = sa.inspect(engine)
        assert {u["name"]: u["column_names"] for u in inspector.get_unique_constraints("scopes")} == {
            "uq_scopes_project_slug": ["project_id", "slug"]
        }
        assert {u["name"]: u["column_names"] for u in inspector.get_unique_constraints("routines")} == {
            "uq_routines_project_name": ["project_id", "name"]
        }
        indexes = {i["name"]: i["column_names"] for t in (*_OWNED, "config_changes") for i in inspector.get_indexes(t)}
        assert indexes["ix_chunks_tenant_project_minted_at_chunk_id"][:2] == ["tenant_id", "project_id"]
        assert indexes["ix_chunks_tenant_minted_at_chunk_id"] == ["tenant_id", "minted_at", "chunk_id"]
        assert indexes["ix_config_changes_tenant_project_id"] == ["tenant_id", "project_id", "id"]
        assert indexes["ix_findings_tenant_project_routine_scope"][:2] == ["tenant_id", "project_id"]
        assert indexes["ix_findings_tenant_routine_scope"] == ["tenant_id", "routine_name", "scope_slug"]
        assert indexes["ix_garden_proposals_tenant_routine_class"] == ["tenant_id", "routine_name", "class"]
        with pytest.raises(IntegrityError), engine.begin() as conn:
            conn.execute(
                sa.text(
                    "INSERT INTO scopes (scope_id, slug, description, created_at, revision, tenant_id, project_id)"
                    " SELECT 'scp_again', slug, '', created_at, 1, tenant_id, project_id FROM scopes"
                )
            )


@pytest.mark.component
@pytest.mark.parametrize("steps", range(1, 8))
def test_a_run_stopped_after_any_step_finishes_as_a_single_run_does(tmp_path: Path, steps: int) -> None:
    (tmp_path / "stopped").mkdir()
    (tmp_path / "reference").mkdir()
    with _at_the_closed_at(tmp_path / "stopped") as (runner, engine):
        _seed(engine)
        _stopped_after(engine, steps)
        kept = _projects(engine) if steps >= 3 else {}

        runner.upgrade(_PROJECTS)

        assert runner.current_revision() == _PROJECTS
        assert {t: p for t, p in _projects(engine).items() if t in kept} == kept
        with _at_the_closed_at(tmp_path / "reference") as (reference_runner, reference):
            _seed(reference)
            reference_runner.upgrade(_PROJECTS)
            assert _shape(engine) == _shape(reference)


@pytest.mark.component
def test_the_fill_pages_in_bounded_batches_and_misses_no_row(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(_revision_module(), "_BATCH_ROWS", 2)
    with _at_the_closed_at(tmp_path) as (runner, engine):
        tenant = _seed(engine, chunks=7)

        runner.upgrade(_PROJECTS)

        project = _projects(engine)[tenant][0]
        assert _query(engine, "SELECT project_id, count(*) FROM chunks GROUP BY project_id") == [(project, 7)]


@pytest.mark.component
def test_the_downgrade_refuses_while_a_tenant_holds_two_projects(tmp_path: Path) -> None:
    with _at_the_closed_at(tmp_path) as (runner, engine):
        tenant = _seed(engine)
        runner.upgrade(_PROJECTS)
        _insert(engine, "projects", [{"project_id": "proj_second", "tenant_id": tenant, "slug": "second"}])

        with pytest.raises(RuntimeError, match=tenant) as refused:
            runner.downgrade(_CLOSED_AT)

        assert _EMPTY not in str(refused.value)
        assert runner.current_revision() == _PROJECTS


@pytest.mark.component
def test_otherwise_the_downgrade_restores_the_store_exactly(tmp_path: Path) -> None:
    with _at_the_closed_at(tmp_path) as (runner, engine):
        _seed(engine)
        snapshot = _schema_snapshot(engine, exemptions={}, name_exemptions={})
        rows = _all_rows(engine)

        runner.upgrade(_PROJECTS)
        runner.downgrade(_CLOSED_AT)

        assert runner.current_revision() == _CLOSED_AT
        assert _schema_snapshot(engine, exemptions={}, name_exemptions={}) == snapshot
        assert _all_rows(engine) == rows


@pytest.mark.unit
@pytest.mark.parametrize(
    "tree,parent_revision,table,columns,direction",
    [
        pytest.param(
            "hub",
            "20261008_1720_tenant_deletion_closed_at",
            "chunks",
            ("project_id",),
            "added",
            id="hub.chunks.project_id",
        ),
        pytest.param(
            "hub",
            "20261008_1720_tenant_deletion_closed_at",
            "scopes",
            ("project_id",),
            "added",
            id="hub.scopes.project_id",
        ),
        pytest.param(
            "hub",
            "20261008_1720_tenant_deletion_closed_at",
            "routines",
            ("project_id",),
            "added",
            id="hub.routines.project_id",
        ),
        pytest.param(
            "hub",
            "20261008_1720_tenant_deletion_closed_at",
            "findings",
            ("project_id",),
            "added",
            id="hub.findings.project_id",
        ),
        pytest.param(
            "hub",
            "20261008_1720_tenant_deletion_closed_at",
            "finding_sets",
            ("project_id",),
            "added",
            id="hub.finding_sets.project_id",
        ),
        pytest.param(
            "hub",
            "20261008_1720_tenant_deletion_closed_at",
            "garden_proposals",
            ("project_id",),
            "added",
            id="hub.garden_proposals.project_id",
        ),
        pytest.param(
            "hub",
            "20261008_1720_tenant_deletion_closed_at",
            "work_item_runs",
            ("project_id",),
            "added",
            id="hub.work_item_runs.project_id",
        ),
        pytest.param(
            "hub",
            "20261008_1720_tenant_deletion_closed_at",
            "work_items",
            ("project_id",),
            "added",
            id="hub.work_items.project_id",
        ),
        pytest.param(
            "hub",
            "20261008_1720_tenant_deletion_closed_at",
            "config_changes",
            ("project_id",),
            "added",
            id="hub.config_changes.project_id",
        ),
    ],
)
def test_frozen_table_lacks_a_later_revisions_columns(
    tree: str, parent_revision: str, table: str, columns: tuple[str, ...], direction: str, tmp_path: Path
) -> None:
    assert_frozen_table_lacks_a_later_revisions_columns(tree, parent_revision, table, columns, direction, tmp_path)

"""The question/decision project revision: every question and decision ends naming the project of its chunk (not
the tenant's first); a run stopped after any step finishes as a single run does; the fill pages in bounded batches;
the new indexes and foreign keys exist; and the downgrade restores the store exactly."""

# pyright: standard

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
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

from blizzard.foundation.clock import FixedClock
from blizzard.foundation.store.engine import create_engine_from_url
from blizzard.foundation.store.migrations import MIGRATION_CLOCK_ATTRIBUTE, MigrationRunner
from blizzard.hub.config import HubConfig
from blizzard.hub.runtime import Migrations
from blizzard.hub.store import MIGRATIONS_DIR
from tests.migration_support_store_migrations import schema_snapshot

pytestmark = [pytest.mark.component]

_PARENT = "20261013_0900_host_fact_lane"

_REVISION = "20261014_0900_question_decision_project"

_T0 = datetime(2026, 1, 1, tzinfo=UTC)

_NOW = datetime(2026, 10, 14, 9, 0, tzinfo=UTC)

_SECOND = "proj_second"


def _revision_module() -> ModuleType:
    revision = ScriptDirectory(str(MIGRATIONS_DIR)).get_revision(_REVISION)
    assert revision is not None
    return revision.module


@contextmanager
def _at_the_parent(tmp_path: Path) -> Iterator[tuple[MigrationRunner, sa.Engine]]:
    db_url = f"sqlite:///{tmp_path / 'hub.db'}"
    runner = Migrations(HubConfig(root=tmp_path, db_url=db_url), FixedClock(_NOW)).runner
    runner.upgrade(_PARENT)
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
            conn.execute(sa.insert(reflected).values({**filled, **row}))


def _query(engine: sa.Engine, sql: str, **params: object) -> list[tuple[Any, ...]]:
    with engine.connect() as conn:
        return [tuple(row) for row in conn.execute(sa.text(sql), params)]


def _seed(engine: sa.Engine, *, chunks: int = 2) -> str:
    """A tenant with two projects; chunks alternate between them, and each holds a question and a decision. Returns
    the id of the tenant's first project."""
    (tenant, home) = _query(engine, "SELECT tenant_id, project_id FROM projects")[0]
    t = {"tenant_id": tenant}
    _insert(engine, "projects", [{**t, "project_id": _SECOND, "slug": "second", "name": "Second", "description": ""}])
    for n in range(chunks):
        project = home if n % 2 == 0 else _SECOND
        _insert(engine, "chunks", [{**t, "chunk_id": f"ch_{n:05d}", "minted_at": _T0, "project_id": project}])
        _insert(engine, "questions", [{**t, "question_id": f"qn_{n:05d}", "chunk_id": f"ch_{n:05d}"}])
        _insert(engine, "decisions", [{"decision_id": f"dec_{n:05d}", "chunk_id": f"ch_{n:05d}", **t}])
    return home


def _stamped(engine: sa.Engine) -> dict[str, list[tuple[Any, ...]]]:
    return {
        "questions": _query(engine, "SELECT question_id, chunk_id, project_id FROM questions ORDER BY question_id"),
        "decisions": _query(engine, "SELECT decision_id, chunk_id, project_id FROM decisions ORDER BY decision_id"),
    }


def _stopped_after(engine: sa.Engine, steps: int) -> None:
    module = _revision_module()
    config = Config()
    config.attributes[MIGRATION_CLOCK_ATTRIBUTE] = FixedClock(_NOW)
    environment = EnvironmentContext(config, ScriptDirectory(str(MIGRATIONS_DIR)))
    with engine.begin() as conn, Operations.context(MigrationContext.configure(conn, environment_context=environment)):
        for step in module.UPGRADE_STEPS[:steps]:
            step(conn)


def _shape(engine: sa.Engine) -> tuple[object, ...]:
    """The store's schema and the question and decision rows, minted ids read as labels."""
    labels = {
        project_id: f"project-{n}"
        for n, (project_id,) in enumerate(_query(engine, "SELECT project_id FROM projects ORDER BY slug"))
    }
    stamped = {
        table: [tuple(labels.get(v, v) for v in row) for row in rows] for table, rows in _stamped(engine).items()
    }
    return schema_snapshot(engine, exemptions={}, name_exemptions={}), stamped


def test_every_question_and_decision_names_its_chunks_project(tmp_path: Path) -> None:
    with _at_the_parent(tmp_path) as (runner, engine):
        home = _seed(engine, chunks=3)

        runner.upgrade(_REVISION)

        for table, key in (("questions", "qn"), ("decisions", "dec")):
            assert _stamped(engine)[table] == [
                (f"{key}_00000", "ch_00000", home),
                (f"{key}_00001", "ch_00001", _SECOND),
                (f"{key}_00002", "ch_00002", home),
            ]


def test_the_columns_are_required_and_keyed_to_projects_beside_new_project_led_indexes(tmp_path: Path) -> None:
    with _at_the_parent(tmp_path) as (runner, engine):
        _seed(engine)

        runner.upgrade(_REVISION)

        inspector = sa.inspect(engine)
        for table in ("questions", "decisions"):
            column = next(c for c in inspector.get_columns(table) if c["name"] == "project_id")
            assert not column["nullable"]
            fks = {fk["name"]: fk["referred_table"] for fk in inspector.get_foreign_keys(table)}
            assert fks[f"fk_{table}_projects"] == "projects"
        indexes = {i["name"]: i["column_names"] for t in ("questions", "decisions") for i in inspector.get_indexes(t)}
        assert indexes["ix_questions_tenant_project_asked_at_question_id"] == [
            "tenant_id",
            "project_id",
            "asked_at",
            "question_id",
        ]
        assert indexes["ix_decisions_tenant_project_submitted_at_decision_id"] == [
            "tenant_id",
            "project_id",
            "submitted_at",
            "decision_id",
        ]
        assert indexes["ix_questions_tenant_asked_at_question_id"] == ["tenant_id", "asked_at", "question_id"]
        assert indexes["ix_decisions_tenant_submitted_at_decision_id"] == ["tenant_id", "submitted_at", "decision_id"]


@pytest.mark.parametrize("steps", range(1, 6))
def test_a_run_stopped_after_any_step_finishes_as_a_single_run_does(tmp_path: Path, steps: int) -> None:
    (tmp_path / "stopped").mkdir()
    (tmp_path / "reference").mkdir()
    with _at_the_parent(tmp_path / "stopped") as (runner, engine):
        _seed(engine)
        _stopped_after(engine, steps)

        runner.upgrade(_REVISION)

        assert runner.current_revision() == _REVISION
        with _at_the_parent(tmp_path / "reference") as (reference_runner, reference):
            _seed(reference)
            reference_runner.upgrade(_REVISION)
            assert _shape(engine) == _shape(reference)


def test_the_fill_pages_in_bounded_batches_and_misses_no_row(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(_revision_module(), "_BATCH_ROWS", 2)
    with _at_the_parent(tmp_path) as (runner, engine):
        home = _seed(engine, chunks=7)

        runner.upgrade(_REVISION)

        for table in ("questions", "decisions"):
            assert sorted(_query(engine, f"SELECT project_id, count(*) FROM {table} GROUP BY project_id")) == sorted(
                [(home, 4), (_SECOND, 3)]
            )


def test_the_downgrade_restores_the_store_exactly(tmp_path: Path) -> None:
    with _at_the_parent(tmp_path) as (runner, engine):
        _seed(engine)
        snapshot = schema_snapshot(engine, exemptions={}, name_exemptions={})
        questions = _query(engine, "SELECT * FROM questions ORDER BY question_id")
        decisions = _query(engine, "SELECT * FROM decisions ORDER BY decision_id")

        runner.upgrade(_REVISION)
        runner.downgrade(_PARENT)

        assert runner.current_revision() == _PARENT
        assert schema_snapshot(engine, exemptions={}, name_exemptions={}) == snapshot
        assert _query(engine, "SELECT * FROM questions ORDER BY question_id") == questions
        assert _query(engine, "SELECT * FROM decisions ORDER BY decision_id") == decisions

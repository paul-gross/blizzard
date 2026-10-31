"""The decision check-results revision: existing decisions read as having run no checks and existing resolutions as no
override; a re-run changes nothing; and the downgrade restores the store exactly."""

# pyright: standard

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
import sqlalchemy as sa

from blizzard.foundation.clock import FixedClock
from blizzard.foundation.store.engine import create_engine_from_url
from blizzard.foundation.store.migrations import MigrationRunner
from blizzard.hub.config import HubConfig
from blizzard.hub.runtime import Migrations
from tests.migration_support_store_migrations import schema_snapshot

pytestmark = [pytest.mark.component]

_PARENT = "20261016_0900_chunk_demoted"

_REVISION = "20261018_0900_decision_check_results"

_T0 = datetime(2026, 1, 1, tzinfo=UTC)

_NOW = datetime(2026, 10, 18, 9, 0, tzinfo=UTC)


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


def _insert(engine: sa.Engine, table: str, row: dict[str, object]) -> None:
    columns = sa.inspect(engine).get_columns(table)
    required = [c for c in columns if not c["nullable"] and c["default"] is None and not c.get("autoincrement")]
    reflected = sa.Table(table, sa.MetaData(), autoload_with=engine)
    with engine.begin() as conn:
        filled = {
            c["name"]: (_T0 if "DATE" in str(c["type"]).upper() or "TIME" in str(c["type"]).upper() else "x")
            for c in required
            if c["name"] not in row
        }
        conn.execute(sa.insert(reflected).values({**filled, **row}))


def _query(engine: sa.Engine, sql: str) -> list[tuple[Any, ...]]:
    with engine.connect() as conn:
        return [tuple(row) for row in conn.execute(sa.text(sql))]


def _seed(engine: sa.Engine) -> None:
    (tenant, project) = _query(engine, "SELECT tenant_id, project_id FROM projects")[0]
    t = {"tenant_id": tenant}
    _insert(engine, "chunks", {**t, "chunk_id": "ch_1", "minted_at": _T0, "project_id": project})
    _insert(engine, "decisions", {**t, "decision_id": "dec_1", "chunk_id": "ch_1", "project_id": project})
    _insert(engine, "decision_resolutions", {**t, "decision_id": "dec_1", "choice": "pass", "resolved_by": "op"})


def test_existing_decisions_run_no_checks_and_existing_resolutions_override_nothing(tmp_path: Path) -> None:
    with _at_the_parent(tmp_path) as (runner, engine):
        _seed(engine)

        runner.upgrade(_REVISION)

        assert _query(engine, "SELECT check_results FROM decisions") == [("[]",)]
        assert [bool(v) for (v,) in _query(engine, "SELECT overrode_red_checks FROM decision_resolutions")] == [False]


def test_a_re_run_changes_nothing(tmp_path: Path) -> None:
    with _at_the_parent(tmp_path) as (runner, engine):
        _seed(engine)
        runner.upgrade(_REVISION)
        snapshot = schema_snapshot(engine, exemptions={}, name_exemptions={})

        runner.downgrade(_PARENT)
        runner.upgrade(_REVISION)

        assert schema_snapshot(engine, exemptions={}, name_exemptions={}) == snapshot


def test_the_downgrade_restores_the_store_exactly(tmp_path: Path) -> None:
    with _at_the_parent(tmp_path) as (runner, engine):
        _seed(engine)
        snapshot = schema_snapshot(engine, exemptions={}, name_exemptions={})
        decisions = _query(engine, "SELECT * FROM decisions")
        resolutions = _query(engine, "SELECT * FROM decision_resolutions")

        runner.upgrade(_REVISION)
        runner.downgrade(_PARENT)

        assert runner.current_revision() == _PARENT
        assert schema_snapshot(engine, exemptions={}, name_exemptions={}) == snapshot
        assert _query(engine, "SELECT * FROM decisions") == decisions
        assert _query(engine, "SELECT * FROM decision_resolutions") == resolutions

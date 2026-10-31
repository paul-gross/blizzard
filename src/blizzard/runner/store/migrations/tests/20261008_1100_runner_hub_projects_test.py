"""Tests proving the ``20261008_1100_runner_hub_projects`` revision."""

# pyright: standard

from __future__ import annotations

from pathlib import Path

import pytest
import sqlalchemy as sa

from blizzard.foundation.store.engine import create_engine_from_url
from blizzard.runner import runtime as runner_runtime
from blizzard.runner.hub.served_projects import ServedProject
from tests.config_inputs import init_host
from tests.migration_support_runner_store_migrations import (
    _LATER,
    _columns,
    _rows,
)
from tests.runner_fakes import SqlAlchemyRunnerStore, runner_store_errors

pytestmark = [pytest.mark.component]

_TENANT_REVISION = "20261008_1000_runner_identity_tenant"


def test_hub_projects_migration_adds_the_answer_and_its_marker_and_drops_both_losslessly(tmp_path: Path) -> None:
    """``20261008_1100_runner_hub_projects``: a carried identity row holds no answer until its next
    registration; down drops the table and the marker, and up again restores an empty, unanswered shape."""
    config = init_host(tmp_path)
    migration = runner_runtime.migration_runner(config)
    engine = create_engine_from_url(config.db_url)
    try:
        migration.downgrade(_TENANT_REVISION)
        assert "hub_served_projects" not in sa.inspect(engine).get_table_names()
        assert "projects_answered_at" not in _columns(engine, "runner_identity")
        with engine.begin() as conn:
            conn.execute(
                sa.text(
                    "INSERT INTO runner_identity (runner_id, runner_name, registered_at) VALUES ('rn_1', 'r', :at)"
                ),
                {"at": _LATER.isoformat()},
            )

        migration.upgrade("head")
        store = SqlAlchemyRunnerStore(engine, runner_store_errors(), "rn_1")
        assert store.served_projects_answer() is None
        store.record_served_projects((ServedProject("default", "prj_1", "default", "Default"),), at=_LATER)
        assert store.served_projects_answer() is not None

        migration.downgrade(_TENANT_REVISION)
        assert "hub_served_projects" not in sa.inspect(engine).get_table_names()
        assert "projects_answered_at" not in _columns(engine, "runner_identity")
        assert _rows(engine, "SELECT runner_id FROM runner_identity") == [("rn_1",)]

        migration.upgrade("head")
        assert store.served_projects_answer() is None
        assert _columns(engine, "hub_served_projects") == {
            "id",
            "position",
            "entry",
            "project_id",
            "slug",
            "name",
            "color",
            "answered_at",
            "runner_id",
        }
    finally:
        engine.dispose()

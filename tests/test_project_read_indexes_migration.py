"""The project read-indexes revision: the five tenant-led garden indexes kept beside the project-led ones are dropped,
the project-led ones and the tenant-wide listings' own indexes stay, a re-run changes nothing, and the downgrade
restores the store exactly."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path

import pytest
import sqlalchemy as sa

from blizzard.foundation.clock import FixedClock
from blizzard.foundation.store.engine import create_engine_from_url
from blizzard.foundation.store.migrations import MigrationRunner
from blizzard.hub.config import HubConfig
from blizzard.hub.runtime import Migrations
from tests.test_store_migrations import _schema_snapshot

pytestmark = pytest.mark.component

_PROJECTS = "20261008_1800_projects"
_READ_INDEXES = "20261008_1810_project_read_indexes"
_NOW = datetime(2026, 10, 8, 18, 10, tzinfo=UTC)
_DROPPED = {
    "findings": {
        "ix_findings_tenant_routine_scope",
        "ix_findings_tenant_routine_class",
        "ix_findings_tenant_scope_source",
    },
    "finding_sets": {"ix_finding_sets_tenant_routine_scope"},
    "garden_proposals": {"ix_garden_proposals_tenant_routine_class"},
}
_KEPT = {
    "findings": {
        "ix_findings_tenant_project_routine_scope",
        "ix_findings_tenant_project_routine_class",
        "ix_findings_tenant_project_scope_source",
    },
    "finding_sets": {"ix_finding_sets_tenant_project_routine_scope"},
    "garden_proposals": {"ix_garden_proposals_tenant_project_routine_class"},
    "scopes": {"ix_scopes_tenant_slug"},
    "routines": {"ix_routines_tenant_name"},
}


@contextmanager
def _at_the_projects(tmp_path: Path) -> Iterator[tuple[MigrationRunner, sa.Engine]]:
    db_url = f"sqlite:///{tmp_path / 'hub.db'}"
    runner = Migrations(HubConfig(root=tmp_path, db_url=db_url), FixedClock(_NOW)).runner
    runner.upgrade(_PROJECTS)
    engine = create_engine_from_url(db_url)
    try:
        yield runner, engine
    finally:
        engine.dispose()


def _indexes(engine: sa.Engine, table: str) -> set[str]:
    return {str(i["name"]) for i in sa.inspect(engine).get_indexes(table)}


def test_the_tenant_led_garden_indexes_go_and_the_project_led_ones_stay(tmp_path: Path) -> None:
    with _at_the_projects(tmp_path) as (runner, engine):
        runner.upgrade(_READ_INDEXES)

        for table, names in _DROPPED.items():
            assert not names & _indexes(engine, table), table
        for table, names in _KEPT.items():
            assert names <= _indexes(engine, table), table


def test_a_rerun_over_a_store_already_reshaped_changes_nothing(tmp_path: Path) -> None:
    with _at_the_projects(tmp_path) as (runner, engine):
        runner.upgrade(_READ_INDEXES)
        schema = _schema_snapshot(engine, exemptions={}, name_exemptions={})
        with engine.begin() as conn:
            conn.execute(sa.text(f"UPDATE alembic_version SET version_num = '{_PROJECTS}'"))

        runner.upgrade(_READ_INDEXES)

        assert _schema_snapshot(engine, exemptions={}, name_exemptions={}) == schema


def test_the_downgrade_restores_the_store_exactly(tmp_path: Path) -> None:
    with _at_the_projects(tmp_path) as (runner, engine):
        schema = _schema_snapshot(engine, exemptions={}, name_exemptions={})
        runner.upgrade(_READ_INDEXES)

        runner.downgrade(_PROJECTS)

        assert _schema_snapshot(engine, exemptions={}, name_exemptions={}) == schema

"""The route-released-cause revision: ``route_released`` gains nullable ``cause`` and ``reason`` columns; a re-run
changes nothing; and the downgrade restores the store exactly."""

from __future__ import annotations

from pathlib import Path

import pytest
import sqlalchemy as sa

from blizzard.foundation.store.engine import create_engine_from_url
from blizzard.hub.store import schema as s
from tests.migration_support_store_migrations import schema_snapshot
from tests.support import migrate_to

pytestmark = [pytest.mark.component]

_PARENT = "20261022_1000_runner_declining"

_CAUSE = "20261023_0900_route_released_cause"


def _columns(engine: sa.Engine, table: str) -> dict[str, bool]:
    return {c["name"]: bool(c["nullable"]) for c in sa.inspect(engine).get_columns(table)}


def test_the_upgrade_adds_both_nullable_columns(tmp_path: Path) -> None:
    runner, engine = migrate_to(tmp_path, _PARENT)

    runner.upgrade(_CAUSE)

    columns = _columns(engine, "route_released")
    assert columns["cause"] is True
    assert columns["reason"] is True


def test_the_new_columns_match_the_declared_schema(tmp_path: Path) -> None:
    _, engine = migrate_to(tmp_path, "head")
    declared = create_engine_from_url(f"sqlite:///{tmp_path / 'declared.db'}")
    try:
        s.metadata.create_all(declared)
        migrated, wanted = _columns(engine, "route_released"), _columns(declared, "route_released")
        assert (migrated["cause"], migrated["reason"]) == (wanted["cause"], wanted["reason"])
    finally:
        declared.dispose()


def test_a_rerun_over_a_store_already_reshaped_changes_nothing(tmp_path: Path) -> None:
    runner, engine = migrate_to(tmp_path, _CAUSE)
    schema = schema_snapshot(engine, exemptions={}, name_exemptions={})
    with engine.begin() as conn:
        conn.execute(sa.text(f"UPDATE alembic_version SET version_num = '{_PARENT}'"))

    runner.upgrade(_CAUSE)

    assert schema_snapshot(engine, exemptions={}, name_exemptions={}) == schema


def test_the_downgrade_restores_the_store_exactly(tmp_path: Path) -> None:
    runner, engine = migrate_to(tmp_path, _PARENT)
    before = schema_snapshot(engine, exemptions={}, name_exemptions={})
    runner.upgrade(_CAUSE)

    runner.downgrade(_PARENT)

    assert schema_snapshot(engine, exemptions={}, name_exemptions={}) == before
    runner.upgrade(_CAUSE)
    assert {"cause", "reason"} <= set(_columns(engine, "route_released"))

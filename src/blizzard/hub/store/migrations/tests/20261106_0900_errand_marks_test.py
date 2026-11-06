"""The errand-marks revision: ``work_item_runs`` gains nullable ``cadence_id`` and ``chore_id`` and a tenant-led index
over ``(tenant_id, chore_id, scope_id)``, ``routine_scopes`` gains a nullable ``linked_at``, all as
``src/blizzard/hub/store/schema.py`` declares them; existing rows survive reading null; a re-run changes nothing; and the
downgrade restores the parent store exactly, after which the upgrade runs again."""

from __future__ import annotations

from pathlib import Path

import pytest
import sqlalchemy as sa

from blizzard.foundation.store.engine import create_engine_from_url
from blizzard.hub.store import schema as s
from tests.support.migrations.store_migrations import schema_snapshot
from tests.support.scaffolding import migrate_to

pytestmark = [pytest.mark.component]

_PARENT = "20261105_1300_cadences"
_REVISION = "20261106_0900_errand_marks"
_RESHAPED = ("work_item_runs", "routine_scopes")


def _snapshot(engine: sa.Engine) -> dict[str, object]:
    return schema_snapshot(engine, exemptions={}, name_exemptions={})


def _seed(conn: sa.Connection) -> None:
    conn.execute(sa.text("PRAGMA foreign_keys = OFF"))
    conn.execute(
        sa.text(
            "INSERT INTO work_item_runs (work_item_id, routine_name, scope_id, mode, tenant_id, project_id) "
            "VALUES ('wi_1', 'sweep', 'sc_1', 'full', 'tenant_1', 'prj_1')"
        )
    )
    conn.execute(
        sa.text("INSERT INTO routine_scopes (routine_id, scope_id, tenant_id) VALUES ('rtn_1', 'sc_1', 'tenant_1')")
    )


def test_the_upgrade_reshapes_both_tables_as_the_schema_declares(tmp_path: Path) -> None:
    runner, engine = migrate_to(tmp_path, _PARENT)
    runner.upgrade(_REVISION)
    declared = create_engine_from_url(f"sqlite:///{tmp_path / 'declared.db'}")
    try:
        s.metadata.create_all(declared)
        migrated, wanted = _snapshot(engine), _snapshot(declared)
        assert {t: migrated[t] for t in _RESHAPED} == {t: wanted[t] for t in _RESHAPED}
    finally:
        declared.dispose()
    indexes = {i["name"]: i["column_names"] for i in sa.inspect(engine).get_indexes("work_item_runs")}
    assert indexes["ix_work_item_runs_tenant_chore_scope"] == ["tenant_id", "chore_id", "scope_id"]


def test_existing_rows_survive_reading_null(tmp_path: Path) -> None:
    runner, engine = migrate_to(tmp_path, _PARENT)
    with engine.begin() as conn:
        _seed(conn)

    runner.upgrade(_REVISION)

    with engine.connect() as conn:
        run = conn.execute(sa.text("SELECT work_item_id, cadence_id, chore_id FROM work_item_runs")).one()
        link = conn.execute(sa.text("SELECT routine_id, linked_at FROM routine_scopes")).one()
    assert tuple(run) == ("wi_1", None, None)
    assert tuple(link) == ("rtn_1", None)


def test_a_rerun_over_a_store_already_reshaped_changes_nothing(tmp_path: Path) -> None:
    runner, engine = migrate_to(tmp_path, _REVISION)
    schema = _snapshot(engine)
    with engine.begin() as conn:
        conn.execute(sa.text(f"UPDATE alembic_version SET version_num = '{_PARENT}'"))

    runner.upgrade(_REVISION)

    assert _snapshot(engine) == schema


def test_the_downgrade_restores_the_parent_store_and_the_upgrade_runs_again(tmp_path: Path) -> None:
    runner, engine = migrate_to(tmp_path, _PARENT)
    with engine.begin() as conn:
        _seed(conn)
    before = _snapshot(engine)
    runner.upgrade(_REVISION)
    after = _snapshot(engine)

    runner.downgrade(_PARENT)

    assert runner.current_revision() == _PARENT
    assert _snapshot(engine) == before
    with engine.connect() as conn:
        assert conn.execute(sa.text("SELECT COUNT(*) FROM work_item_runs")).scalar() == 1
        assert conn.execute(sa.text("SELECT COUNT(*) FROM routine_scopes")).scalar() == 1
    runner.upgrade(_REVISION)
    assert runner.current_revision() == _REVISION
    assert _snapshot(engine) == after

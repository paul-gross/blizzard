"""The urgency-references revision: ``routines`` gains a nullable ``default_urgency_id`` and ``cadence_chores`` a
nullable ``urgency_id``, as ``src/blizzard/hub/store/schema.py`` declares them; existing rows survive reading null; a
re-run changes nothing; and the downgrade restores the parent store exactly, after which the upgrade runs again."""

from __future__ import annotations

from pathlib import Path

import pytest
import sqlalchemy as sa

from blizzard.foundation.store.engine import create_engine_from_url
from blizzard.hub.store import schema as s
from tests.support.migrations.store_migrations import schema_snapshot
from tests.support.scaffolding import migrate_to

pytestmark = [pytest.mark.component]

_PARENT = "20261108_0900_urgencies"
_REVISION = "20261109_0900_urgency_references"
_RESHAPED = ("routines", "cadence_chores")


def _snapshot(engine: sa.Engine) -> dict[str, object]:
    return schema_snapshot(engine, exemptions={}, name_exemptions={})


def _seed(conn: sa.Connection) -> None:
    conn.execute(sa.text("PRAGMA foreign_keys = OFF"))
    conn.execute(
        sa.text(
            "INSERT INTO routines (routine_id, name, slug, graph_name, default_scope_id, created_at, revision, "
            "tenant_id, project_id) VALUES ('rtn_1', 'sweep', 'sweep', 'g', 'sc_1', '2026-11-01', 1, 'tenant_1', "
            "'prj_1')"
        )
    )
    conn.execute(
        sa.text(
            "INSERT INTO cadence_chores (chore_id, cadence_id, key, routine_slug, scope_selection, scopes_set_at, "
            "mode, promote, position, created_at, tenant_id) VALUES ('chr_1', 'cad_1', 'nightly', 'sweep', "
            "'default', '2026-11-01', 'full', 1, 0, '2026-11-01', 'tenant_1')"
        )
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


def test_existing_rows_survive_reading_null(tmp_path: Path) -> None:
    runner, engine = migrate_to(tmp_path, _PARENT)
    with engine.begin() as conn:
        _seed(conn)

    runner.upgrade(_REVISION)

    with engine.connect() as conn:
        routine = conn.execute(sa.text("SELECT routine_id, default_urgency_id FROM routines")).one()
        chore = conn.execute(sa.text("SELECT chore_id, urgency_id FROM cadence_chores")).one()
    assert tuple(routine) == ("rtn_1", None)
    assert tuple(chore) == ("chr_1", None)


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
        assert conn.execute(sa.text("SELECT COUNT(*) FROM routines")).scalar() == 1
        assert conn.execute(sa.text("SELECT COUNT(*) FROM cadence_chores")).scalar() == 1
    runner.upgrade(_REVISION)
    assert runner.current_revision() == _REVISION
    assert _snapshot(engine) == after

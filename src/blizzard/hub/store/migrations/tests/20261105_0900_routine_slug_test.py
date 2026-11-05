"""The slug revision fills each routine's slug from its name, leaves lineage rows alone, and walks back."""

from __future__ import annotations

from pathlib import Path

import pytest
import sqlalchemy as sa

from tests.support.scaffolding import migrate_to

pytestmark = pytest.mark.component

_PARENT = "20261104_0900_drop_graph_nodes_run"
_REVISION = "20261105_0900_routine_slug"
_NAMES = ("Arch Sweep", "docs-sweep")


def _seed(conn: sa.Connection) -> None:
    conn.execute(sa.text("PRAGMA foreign_keys = OFF"))
    for index, name in enumerate(_NAMES):
        conn.execute(
            sa.text(
                "INSERT INTO routines (routine_id, name, graph_name, default_scope_id, created_at, revision, "
                "tenant_id, project_id) VALUES (:id, :name, 'g', 'sc_1', '2026-11-01', 1, 'tenant_1', 'prj_1')"
            ),
            {"id": f"rtn_{index}", "name": name},
        )
        conn.execute(
            sa.text(
                "INSERT INTO findings (finding_id, routine_name, scope_id, scope_slug, class, locus, summary, "
                "source, tenant_id, project_id) VALUES (:id, :name, 'sc_1', 'core', 'c', 'a.py', 's', 'routine', 'tenant_1', 'prj_1')"
            ),
            {"id": f"fin_{index}", "name": name},
        )
        conn.execute(
            sa.text(
                "INSERT INTO finding_sets (finding_set_id, artifact_id, chunk_id, scope_id, scope_slug, "
                "routine_name, revisions, tenant_id, project_id) VALUES (:id, :id, 'ch_1', 'sc_1', 'core', :name, "
                "'{}', 'tenant_1', 'prj_1')"
            ),
            {"id": f"fins_{index}", "name": name},
        )
        conn.execute(
            sa.text(
                "INSERT INTO work_item_runs (work_item_id, routine_name, scope_id, mode, tenant_id, project_id) "
                "VALUES (:id, :name, 'sc_1', 'full', 'tenant_1', 'prj_1')"
            ),
            {"id": f"wi_{index}", "name": name},
        )


def _slugs(engine: sa.Engine) -> dict[str, str]:
    with engine.connect() as conn:
        return {str(r.name): str(r.slug) for r in conn.execute(sa.text("SELECT name, slug FROM routines"))}


_LINEAGE_TABLES = ("findings", "finding_sets", "work_item_runs")


def _lineage(engine: sa.Engine) -> dict[str, list[str]]:
    with engine.connect() as conn:
        return {
            table: sorted(conn.execute(sa.text(f"SELECT routine_name FROM {table}")).scalars())
            for table in _LINEAGE_TABLES
        }


def test_upgrade_fills_slug_from_name_and_round_trips(tmp_path: Path) -> None:
    runner, engine = migrate_to(tmp_path, _PARENT)
    with engine.begin() as conn:
        _seed(conn)

    runner.upgrade(_REVISION)
    inspector = sa.inspect(engine)
    assert _slugs(engine) == {name: name for name in _NAMES}
    assert _lineage(engine) == {table: sorted(_NAMES) for table in _LINEAGE_TABLES}
    assert {u["name"] for u in inspector.get_unique_constraints("routines")} == {"uq_routines_project_slug"}
    assert {i["name"]: i["column_names"] for i in inspector.get_indexes("routines")} == {
        "ix_routines_tenant_slug": ["tenant_id", "slug"]
    }
    slug = next(c for c in inspector.get_columns("routines") if c["name"] == "slug")
    assert not slug["nullable"]

    with engine.begin() as conn:
        conn.execute(sa.text("UPDATE routines SET name = 'Edited' WHERE slug = 'docs-sweep'"))
    runner.downgrade(_PARENT)
    inspector = sa.inspect(engine)
    assert "slug" not in {c["name"] for c in inspector.get_columns("routines")}
    assert {u["name"] for u in inspector.get_unique_constraints("routines")} == {"uq_routines_project_name"}
    assert {i["name"] for i in inspector.get_indexes("routines")} == {"ix_routines_tenant_name"}
    with engine.connect() as conn:
        names = sorted(conn.execute(sa.text("SELECT name FROM routines")).scalars())
    assert names == sorted(_NAMES)

    runner.upgrade(_REVISION)
    assert _slugs(engine) == {name: name for name in _NAMES}

"""The drop-graph-nodes-run revision: ``graph_nodes`` loses ``run``; a re-run changes nothing; and the downgrade
re-adds it, giving each hub row its ``legacy_run`` or the step list its action was in, none for ``pass``, with the
schema as before — a test action refusing the downgrade, naming its row, and changing nothing."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest
import sqlalchemy as sa

from blizzard.foundation.store.engine import create_engine_from_url
from blizzard.foundation.store.migrations import MigrationRunner
from blizzard.hub.store import schema as s
from tests.support.migrations.store_migrations import schema_snapshot
from tests.support.scaffolding import default_tenant, migrate_to

pytestmark = [pytest.mark.component]

_PARENT = "20261102_0900_drop_hub_exec_slot"
_DROP = "20261104_0900_drop_graph_nodes_run"

_AT = datetime(2026, 11, 4, tzinfo=UTC)
_SCRIPTS = "python3 -m blizzard.hub.graphs.scripts."
_LEGACY = json.dumps([{"command": "./notify.sh", "name": "notify", "produces": None}])


def _step(name: str, command: str) -> str:
    return json.dumps([{"command": command, "name": name, "produces": None}])


# node id -> (executor, action, action_args, legacy_run) as seeded at the parent revision, every `run` NULL.
_SEEDED: dict[str, tuple[str, str | None, str | None, str | None]] = {
    "nd_land": ("hub", "land", None, None),
    "nd_review": ("hub", "record-findings", None, None),
    "nd_garden": ("hub", "garden-deliver", json.dumps({"delta": ["one delta"], "proposals": ["docket", "more"]}), None),
    "nd_pass": ("hub", "pass", None, None),
    "nd_kept": ("hub", None, None, _LEGACY),
    "nd_build": ("runner", None, None, None),
}

# node id -> `run` once downgraded.
_RESTORED: dict[str, str | None] = {
    "nd_land": _step("land-every-repo", f"{_SCRIPTS}land_pr_ci"),
    "nd_review": _step("materialize-review-findings", f"{_SCRIPTS}review_deliver"),
    "nd_garden": _step(
        "materialize-garden-artifacts",
        f"{_SCRIPTS}garden_deliver --delta 'one delta' --proposals docket --proposals more",
    ),
    "nd_pass": None,
    "nd_kept": _LEGACY,
    "nd_build": None,
}


def _graph(engine: sa.Engine, tenant: str) -> None:
    with engine.begin() as conn:
        conn.execute(
            sa.text(
                "INSERT INTO graphs (graph_id, name, entry_node_id, definition_yaml, created_at, tenant_id)"
                " VALUES ('gr_a', 'g', 'nd_land', '', :at, :tenant)"
            ),
            {"at": _AT, "tenant": tenant},
        )


def _node(engine: sa.Engine, tenant: str, node_id: str, **extra: str | None) -> None:
    columns = {
        "node_id": node_id,
        "graph_id": "gr_a",
        "name": node_id,
        "session": "resume",
        "judged_by": "worker",
        "tenant_id": tenant,
        **extra,
    }
    with engine.begin() as conn:
        conn.execute(
            sa.text(f"INSERT INTO graph_nodes ({', '.join(columns)}) VALUES ({', '.join(':' + c for c in columns)})"),
            columns,
        )


def _seeded(tmp_path: Path) -> tuple[MigrationRunner, sa.Engine, str]:
    runner, engine = migrate_to(tmp_path, _PARENT)
    tenant = default_tenant(engine)
    _graph(engine, tenant)
    for node_id, (executor, action, args, legacy) in _SEEDED.items():
        _node(engine, tenant, node_id, executor=executor, action=action, action_args=args, legacy_run=legacy)
    return runner, engine, tenant


def _columns(engine: sa.Engine) -> set[str]:
    return {str(c["name"]) for c in sa.inspect(engine).get_columns("graph_nodes")}


def _rows(engine: sa.Engine, *columns: str) -> dict[str, tuple[object, ...]]:
    with engine.connect() as conn:
        rows = conn.execute(sa.text(f"SELECT node_id, {', '.join(columns)} FROM graph_nodes")).all()
    return {row[0]: tuple(row[1:]) for row in rows}


def test_the_upgrade_drops_the_column_and_keeps_every_row(tmp_path: Path) -> None:
    runner, engine, _ = _seeded(tmp_path)
    kept = _rows(engine, "executor", "action", "action_args", "legacy_run")

    runner.upgrade(_DROP)

    assert "run" not in _columns(engine)
    assert _rows(engine, "executor", "action", "action_args", "legacy_run") == kept


def test_the_dropped_column_matches_the_declared_schema(tmp_path: Path) -> None:
    _, engine = migrate_to(tmp_path, "head")
    declared = create_engine_from_url(f"sqlite:///{tmp_path / 'declared.db'}")
    try:
        s.metadata.create_all(declared)
        wanted = {str(c["name"]) for c in sa.inspect(declared).get_columns("graph_nodes")}
        assert "run" not in wanted
        assert _columns(engine) == wanted
    finally:
        declared.dispose()


def test_a_rerun_changes_nothing(tmp_path: Path) -> None:
    runner, engine, _ = _seeded(tmp_path)
    runner.upgrade(_DROP)
    schema = schema_snapshot(engine, exemptions={}, name_exemptions={})
    rows = _rows(engine, "executor", "action", "action_args", "legacy_run")
    with engine.begin() as conn:
        conn.execute(sa.text(f"UPDATE alembic_version SET version_num = '{_PARENT}'"))

    runner.upgrade(_DROP)

    assert schema_snapshot(engine, exemptions={}, name_exemptions={}) == schema
    assert _rows(engine, "executor", "action", "action_args", "legacy_run") == rows


def test_the_downgrade_refills_run_from_legacy_run_and_the_inverse_map(tmp_path: Path) -> None:
    runner, engine, _ = _seeded(tmp_path)
    before = schema_snapshot(engine, exemptions={}, name_exemptions={})
    runner.upgrade(_DROP)

    runner.downgrade(_PARENT)

    assert schema_snapshot(engine, exemptions={}, name_exemptions={}) == before
    assert {node_id: run for node_id, (run,) in _rows(engine, "run").items()} == _RESTORED


def test_a_test_action_refuses_the_downgrade_naming_its_row_and_changes_nothing(tmp_path: Path) -> None:
    runner, engine, tenant = _seeded(tmp_path)
    _node(engine, tenant, "nd_test", executor="hub", action="test.answer", action_args=json.dumps({"choice": ["x"]}))
    runner.upgrade(_DROP)
    schema = schema_snapshot(engine, exemptions={}, name_exemptions={})

    with pytest.raises(RuntimeError, match="nd_test"):
        runner.downgrade(_PARENT)

    assert schema_snapshot(engine, exemptions={}, name_exemptions={}) == schema
    assert "run" not in _columns(engine)

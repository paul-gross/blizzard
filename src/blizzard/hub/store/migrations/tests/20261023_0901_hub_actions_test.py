"""The hub-actions revision: ``graph_nodes`` gains nullable ``action`` and ``action_args`` and ``hub_node_poll`` a
nullable ``next_poll_at``, every existing row reading none; a re-run changes nothing; and the downgrade gives every hub
node naming an action and no ``run`` the step list its action was in, refusing a test action, before restoring the
store exactly."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest
import sqlalchemy as sa

from blizzard.foundation.store.engine import create_engine_from_url
from blizzard.hub.store import schema as s
from tests.migration_support_store_migrations import _schema_snapshot
from tests.support import default_tenant, migrate_to

pytestmark = [pytest.mark.component]

_PARENT = "20261023_0900_route_released_cause"

_ACTIONS = "20261023_0901_hub_actions"

_AT = datetime(2026, 10, 23, tzinfo=UTC)


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
        "executor": "hub",
        "session": "resume",
        "judged_by": "worker",
        "tenant_id": tenant,
        **extra,
    }
    names = ", ".join(columns)
    with engine.begin() as conn:
        conn.execute(
            sa.text(f"INSERT INTO graph_nodes ({names}) VALUES ({', '.join(':' + c for c in columns)})"), columns
        )


def _runs(engine: sa.Engine) -> dict[str, object]:
    with engine.connect() as conn:
        rows = conn.execute(sa.text("SELECT node_id, run FROM graph_nodes")).all()
    return {node_id: json.loads(run) if run else None for node_id, run in rows}


def _columns(engine: sa.Engine, table: str) -> dict[str, bool]:
    return {c["name"]: bool(c["nullable"]) for c in sa.inspect(engine).get_columns(table)}


def test_the_upgrade_adds_the_columns_and_writes_no_row(tmp_path: Path) -> None:
    runner, engine = migrate_to(tmp_path, _PARENT)
    tenant = default_tenant(engine)
    _graph(engine, tenant)
    _node(engine, tenant, "nd_run", run=json.dumps([{"command": "true", "name": None, "produces": None}]))

    runner.upgrade(_ACTIONS)

    assert _columns(engine, "graph_nodes")["action"] is True
    assert _columns(engine, "graph_nodes")["action_args"] is True
    assert _columns(engine, "hub_node_poll")["next_poll_at"] is True
    with engine.connect() as conn:
        assert conn.execute(sa.text("SELECT action, action_args FROM graph_nodes")).one() == (None, None)


def test_the_new_columns_match_the_declared_schema(tmp_path: Path) -> None:
    _, engine = migrate_to(tmp_path, "head")
    declared = create_engine_from_url(f"sqlite:///{tmp_path / 'declared.db'}")
    try:
        s.metadata.create_all(declared)
        for table, column in (
            ("graph_nodes", "action"),
            ("graph_nodes", "action_args"),
            ("hub_node_poll", "next_poll_at"),
        ):
            assert _columns(engine, table)[column] == _columns(declared, table)[column]
    finally:
        declared.dispose()


def test_a_rerun_over_a_store_already_reshaped_changes_nothing(tmp_path: Path) -> None:
    runner, engine = migrate_to(tmp_path, _ACTIONS)
    schema = _schema_snapshot(engine, exemptions={}, name_exemptions={})
    with engine.begin() as conn:
        conn.execute(sa.text(f"UPDATE alembic_version SET version_num = '{_PARENT}'"))

    runner.upgrade(_ACTIONS)

    assert _schema_snapshot(engine, exemptions={}, name_exemptions={}) == schema


def test_the_downgrade_gives_an_action_only_row_its_inverse_run_and_restores_the_store(tmp_path: Path) -> None:
    runner, engine = migrate_to(tmp_path, _PARENT)
    before = _schema_snapshot(engine, exemptions={}, name_exemptions={})
    runner.upgrade(_ACTIONS)
    tenant = default_tenant(engine)
    _graph(engine, tenant)
    kept = [{"command": "true", "name": "own", "produces": None}]
    _node(engine, tenant, "nd_land", action="land")
    _node(engine, tenant, "nd_review", action="record-findings")
    _node(
        engine,
        tenant,
        "nd_garden",
        action="garden-deliver",
        action_args=json.dumps({"delta": ["delta"], "proposals": ["docket", "more"]}),
    )
    _node(engine, tenant, "nd_pass", action="pass")
    _node(engine, tenant, "nd_run", run=json.dumps(kept))

    runner.downgrade(_PARENT)

    assert _schema_snapshot(engine, exemptions={}, name_exemptions={}) == before
    scripts = "python3 -m blizzard.hub.graphs.scripts."
    assert _runs(engine) == {
        "nd_land": [{"command": f"{scripts}land_pr_ci", "name": "land-every-repo", "produces": None}],
        "nd_review": [{"command": f"{scripts}review_deliver", "name": "materialize-review-findings", "produces": None}],
        "nd_garden": [
            {
                "command": f"{scripts}garden_deliver --delta delta --proposals docket --proposals more",
                "name": "materialize-garden-artifacts",
                "produces": None,
            }
        ],
        "nd_pass": None,
        "nd_run": kept,
    }
    runner.upgrade(_ACTIONS)
    assert "action" in _columns(engine, "graph_nodes")


def test_the_downgrade_refuses_a_test_action_naming_its_row(tmp_path: Path) -> None:
    runner, engine = migrate_to(tmp_path, _ACTIONS)
    tenant = default_tenant(engine)
    _graph(engine, tenant)
    _node(engine, tenant, "nd_test", action="test.answer", action_args=json.dumps({"choice": ["success"]}))

    with pytest.raises(RuntimeError, match="nd_test"):
        runner.downgrade(_PARENT)

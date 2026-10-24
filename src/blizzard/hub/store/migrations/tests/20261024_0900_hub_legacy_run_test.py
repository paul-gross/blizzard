"""The hub-legacy-run revision: ``graph_nodes`` gains a nullable ``legacy_run``; every hub row naming no action is
carried onto the action its ``run`` named, or keeps that ``run`` in ``legacy_run`` with one log line; ``run`` is never
written; a re-run changes nothing; and the downgrade restores every row as seeded, the parent's own refusal of a test
action still firing past it."""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from pathlib import Path

import pytest
import sqlalchemy as sa

from blizzard.foundation.store.engine import create_engine_from_url
from blizzard.foundation.store.migrations import MigrationRunner
from blizzard.hub.store import schema as s
from tests.migration_support_store_migrations import _schema_snapshot
from tests.support import default_tenant, migrate_to

pytestmark = [pytest.mark.component]

_GRANDPARENT = "20261023_0900_route_released_cause"
_PARENT = "20261023_0901_hub_actions"
_LEGACY = "20261024_0900_hub_legacy_run"

_AT = datetime(2026, 10, 23, tzinfo=UTC)
_SCRIPTS = "python3 -m blizzard.hub.graphs.scripts."


def _steps(*commands: str) -> str:
    return json.dumps([{"command": c, "name": f"step-{i}", "produces": None} for i, c in enumerate(commands)])


# node id -> (run, action, action_args) as seeded at the parent revision.
_SEEDED: dict[str, tuple[str | None, str | None, str | None]] = {
    "nd_pr_ci": (_steps(f"{_SCRIPTS}land_pr_ci"), None, None),
    "nd_default": (_steps(f"{_SCRIPTS}land_default"), None, None),
    "nd_ff": (_steps("  python  -m   blizzard.hub.graphs.scripts.land_ff  "), None, None),
    "nd_review": (_steps(f"{_SCRIPTS}review_deliver"), None, None),
    "nd_garden": (
        _steps(f"{_SCRIPTS}garden_deliver --delta 'one delta' --proposals docket --proposals=more"),
        None,
        None,
    ),
    "nd_empty": (json.dumps([]), None, None),
    "nd_null": (None, None, None),
    "nd_kept": (_steps(f"{_SCRIPTS}land_pr_ci", "./notify.sh"), None, None),
    "nd_minted": (None, "land", None),
}

# node id -> (action, action_args) once carried.
_CARRIED: dict[str, tuple[str | None, dict[str, list[str]] | None]] = {
    "nd_pr_ci": ("land", None),
    "nd_default": ("land", None),
    "nd_ff": ("land", None),
    "nd_review": ("record-findings", None),
    "nd_garden": ("garden-deliver", {"delta": ["one delta"], "proposals": ["docket", "more"]}),
    "nd_empty": ("pass", None),
    "nd_null": ("pass", None),
    "nd_kept": (None, None),
    "nd_minted": ("land", None),
}


def _graph(engine: sa.Engine, tenant: str) -> None:
    with engine.begin() as conn:
        conn.execute(
            sa.text(
                "INSERT INTO graphs (graph_id, name, entry_node_id, definition_yaml, created_at, tenant_id)"
                " VALUES ('gr_a', 'carried', 'nd_pr_ci', '', :at, :tenant)"
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
    with engine.begin() as conn:
        conn.execute(
            sa.text(f"INSERT INTO graph_nodes ({', '.join(columns)}) VALUES ({', '.join(':' + c for c in columns)})"),
            columns,
        )


def _seeded(tmp_path: Path) -> tuple[MigrationRunner, sa.Engine, str]:
    runner, engine = migrate_to(tmp_path, _PARENT)
    tenant = default_tenant(engine)
    _graph(engine, tenant)
    for node_id, (run, action, args) in _SEEDED.items():
        _node(engine, tenant, node_id, run=run, action=action, action_args=args)
    return runner, engine, tenant


def _rows(engine: sa.Engine, *columns: str) -> dict[str, tuple[object, ...]]:
    with engine.connect() as conn:
        rows = conn.execute(sa.text(f"SELECT node_id, {', '.join(columns)} FROM graph_nodes")).all()
    return {row[0]: tuple(row[1:]) for row in rows}


def test_the_upgrade_carries_every_translatable_row_and_keeps_the_rest(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    runner, engine, tenant = _seeded(tmp_path)

    with caplog.at_level(logging.INFO, logger="alembic.runtime.migration"):
        runner.upgrade(_LEGACY)

    rows = _rows(engine, "action", "action_args", "legacy_run", "run")
    assert {
        node_id: (action, json.loads(str(args)) if args else None) for node_id, (action, args, _, _) in rows.items()
    } == _CARRIED
    assert {node_id: legacy for node_id, (_, _, legacy, _) in rows.items() if legacy is not None} == {
        "nd_kept": _SEEDED["nd_kept"][0]
    }
    assert {node_id: run for node_id, (_, _, _, run) in rows.items()} == {k: v[0] for k, v in _SEEDED.items()}
    lines = [r.getMessage() for r in caplog.records if "kept as legacy_run" in r.getMessage()]
    assert len(lines) == 1
    for part in ("gr_a", "'carried'", "2026-10-23", "'nd_kept'", tenant):
        assert part in lines[0]


def test_the_new_column_matches_the_declared_schema(tmp_path: Path) -> None:
    _, engine = migrate_to(tmp_path, "head")
    declared = create_engine_from_url(f"sqlite:///{tmp_path / 'declared.db'}")
    try:
        s.metadata.create_all(declared)
        held = {c["name"]: c["nullable"] for c in sa.inspect(engine).get_columns("graph_nodes")}
        wanted = {c["name"]: c["nullable"] for c in sa.inspect(declared).get_columns("graph_nodes")}
        assert held["legacy_run"] == wanted["legacy_run"] is True
    finally:
        declared.dispose()


def test_a_rerun_changes_nothing(tmp_path: Path) -> None:
    runner, engine, _ = _seeded(tmp_path)
    runner.upgrade(_LEGACY)
    schema = _schema_snapshot(engine, exemptions={}, name_exemptions={})
    rows = _rows(engine, "action", "action_args", "legacy_run", "run")
    with engine.begin() as conn:
        conn.execute(sa.text(f"UPDATE alembic_version SET version_num = '{_PARENT}'"))

    runner.upgrade(_LEGACY)

    assert _schema_snapshot(engine, exemptions={}, name_exemptions={}) == schema
    assert _rows(engine, "action", "action_args", "legacy_run", "run") == rows


def test_the_downgrade_restores_every_row_as_seeded_and_the_parent_still_refuses_a_test_action(
    tmp_path: Path,
) -> None:
    runner, engine, tenant = _seeded(tmp_path)
    _node(engine, tenant, "nd_test", action="test.answer", action_args=json.dumps({"choice": ["success"]}))
    before = _schema_snapshot(engine, exemptions={}, name_exemptions={})
    seeded = _rows(engine, "run", "action", "action_args")
    runner.upgrade(_LEGACY)

    runner.downgrade(_PARENT)

    assert _schema_snapshot(engine, exemptions={}, name_exemptions={}) == before
    # A row translated from no `run` keeps `pass`, which the parent's own downgrade gives back as no `run`.
    assert _rows(engine, "run", "action", "action_args") == {**seeded, "nd_null": (None, "pass", None)}
    with pytest.raises(RuntimeError, match="nd_test"):
        runner.downgrade(_GRANDPARENT)

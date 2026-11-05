"""The binding-agents-retired revision: the upgrade strips ``agents`` from every stored binding, leaving the runners
that declared no bindings untouched; the downgrade writes the runner's shared count back so the earlier hub can read
every binding."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest
import sqlalchemy as sa

from tests.support.scaffolding import default_tenant, migrate_to

pytestmark = [pytest.mark.component]

_PARENT = "20261102_0900_drop_hub_exec_slot"

_RETIRED = "20261104_0900_binding_agents_retired"

_AT = datetime(2026, 11, 4, tzinfo=UTC)


def _runner(
    engine: sa.Engine, tenant: str, runner_id: str, bindings: list[dict[str, object]] | None, agents: int | None
) -> None:
    with engine.begin() as conn:
        conn.execute(
            sa.text(
                "INSERT INTO runner_registrations (runner_id, name, added_at, tenant_id, bindings, agents)"
                " VALUES (:id, :id, :at, :tenant, :bindings, :agents)"
            ),
            {
                "id": runner_id,
                "at": _AT,
                "tenant": tenant,
                "bindings": None if bindings is None else json.dumps(bindings),
                "agents": agents,
            },
        )


def _bindings(engine: sa.Engine, runner_id: str) -> list[dict[str, object]] | None:
    with engine.connect() as conn:
        stored = conn.execute(
            sa.text("SELECT bindings FROM runner_registrations WHERE runner_id = :id"), {"id": runner_id}
        ).scalar_one()
    return None if stored is None else json.loads(stored)


def test_the_upgrade_strips_agents_from_each_binding_and_skips_a_runner_with_none(tmp_path: Path) -> None:
    runner, engine = migrate_to(tmp_path, _PARENT)
    tenant = default_tenant(engine)
    _runner(
        engine,
        tenant,
        "rn_a",
        [{"project": "p", "workspace_id": "w", "agents": 2}, {"project": "q", "workspace_id": "w", "agents": 1}],
        3,
    )
    _runner(engine, tenant, "rn_none", None, None)

    runner.upgrade(_RETIRED)

    assert _bindings(engine, "rn_a") == [{"project": "p", "workspace_id": "w"}, {"project": "q", "workspace_id": "w"}]
    assert _bindings(engine, "rn_none") is None


def test_the_downgrade_writes_the_runners_count_back_into_each_binding(tmp_path: Path) -> None:
    runner, engine = migrate_to(tmp_path, _PARENT)
    tenant = default_tenant(engine)
    _runner(engine, tenant, "rn_a", [{"project": "p", "workspace_id": "w", "agents": 2}], 3)
    _runner(engine, tenant, "rn_unset", [{"project": "p", "workspace_id": "w", "agents": 2}], None)
    runner.upgrade(_RETIRED)

    runner.downgrade(_PARENT)

    assert _bindings(engine, "rn_a") == [{"project": "p", "workspace_id": "w", "agents": 3}]
    assert _bindings(engine, "rn_unset") == [{"project": "p", "workspace_id": "w", "agents": 1}]

"""Strip the retired per-binding ``agents`` from every stored runner binding.

A runner's agents are one shared count, ``runner_registrations.agents``; a binding no longer carries its own. The
downgrade writes the shared count back into each binding (``1`` where the runner declared none), because the earlier
hub reads ``agents`` off every binding and cannot parse a binding without it. The per-binding value it restores is
lossy; the runner's next registration rewrites its bindings.

Revision ID: 20261104_0900_binding_agents_retired
Revises: 20261102_0900_drop_hub_exec_slot
"""

from __future__ import annotations

import json
from collections.abc import Callable, Sequence
from typing import Any

import sqlalchemy as sa
from alembic import op

revision: str = "20261104_0900_binding_agents_retired"
down_revision: str | None = "20261102_0900_drop_hub_exec_slot"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _rewrite(entry: Callable[[dict[str, Any], int | None], dict[str, Any]]) -> None:
    conn = op.get_bind()
    rows = conn.execute(
        sa.text("SELECT runner_id, tenant_id, bindings, agents FROM runner_registrations WHERE bindings IS NOT NULL")
    ).all()
    for runner_id, tenant_id, bindings, agents in rows:
        rewritten = json.dumps([entry(b, agents) for b in json.loads(bindings)])
        conn.execute(
            sa.text(
                "UPDATE runner_registrations SET bindings = :bindings"
                " WHERE runner_id = :runner_id AND tenant_id = :tenant_id"
            ),
            {"bindings": rewritten, "runner_id": runner_id, "tenant_id": tenant_id},
        )


def upgrade() -> None:
    _rewrite(lambda b, _agents: {k: v for k, v in b.items() if k != "agents"})


def downgrade() -> None:
    _rewrite(lambda b, agents: {**b, "agents": agents if agents is not None else 1})

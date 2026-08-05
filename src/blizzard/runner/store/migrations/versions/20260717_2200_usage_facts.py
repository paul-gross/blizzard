"""usage facts — one append-only row per harness invocation's cost/token telemetry, never
a stored aggregate (runner store tree, issue #58, ``bzh:facts-not-status``)

Revision ID: 20260717_2200_runner_usage_facts
Revises: 20260717_0446_runner_pause_parks
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

from blizzard.runner.store.schema import usage_facts

revision: str = "20260717_2200_runner_usage_facts"
down_revision: str | None = "20260717_2300_runner_requeues"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLES = (usage_facts,)


def upgrade() -> None:
    bind = op.get_bind()
    for table in _TABLES:
        table.create(bind, checkfirst=True)


def downgrade() -> None:
    bind = op.get_bind()
    for table in reversed(_TABLES):
        table.drop(bind, checkfirst=True)

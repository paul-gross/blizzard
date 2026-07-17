"""walking-skeleton fact tables (runner store tree)

The runner store's first real schema (P6): leases (with pid + process-start-time),
chunk->env bindings, and the store-and-forward outbound buffer. Facts only, status
derived (``bzh:facts-not-status``). Defined once in
``blizzard.runner.store.schema``; this revision creates exactly its subset.

Revision ID: 20260713_1218_runner_walking_skeleton
Revises: 20260713_1112_runner_initial
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

from blizzard.runner.store.schema import env_bindings, leases, outbound_buffer

revision: str = "20260713_1218_runner_walking_skeleton"
down_revision: str | None = "20260713_1112_runner_initial"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLES = [leases, env_bindings, outbound_buffer]


def upgrade() -> None:
    bind = op.get_bind()
    for table in _TABLES:
        table.create(bind, checkfirst=False)


def downgrade() -> None:
    bind = op.get_bind()
    for table in reversed(_TABLES):
        table.drop(bind, checkfirst=False)

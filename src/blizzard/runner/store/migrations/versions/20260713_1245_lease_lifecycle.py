"""lease-lifecycle fact tables (runner store tree) — three append-only facts: each attempt's node
context, lease closures (an active lease has none), and binding releases (a held env has none).

Revision ID: 20260713_1245_runner_lease_lifecycle
Revises: 20260713_1218_runner_walking_skeleton
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

from blizzard.runner.store.schema import binding_releases, lease_closures, lease_context

revision: str = "20260713_1245_runner_lease_lifecycle"
down_revision: str | None = "20260713_1218_runner_walking_skeleton"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLES = [lease_context, lease_closures, binding_releases]


def upgrade() -> None:
    bind = op.get_bind()
    for table in _TABLES:
        table.create(bind, checkfirst=False)


def downgrade() -> None:
    bind = op.get_bind()
    for table in reversed(_TABLES):
        table.drop(bind, checkfirst=False)

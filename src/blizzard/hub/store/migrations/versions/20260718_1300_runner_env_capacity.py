"""runner environment-pool capacity: env_capacity on the registry (hub store tree)

Issue #69 has the runner report its configured environment-pool size (the length of its
``workspace_envs``) so the board's fleet registry can render a ``used/total`` slot bar.
This revision adds the column that total lands in.

Nullable: a runner registered by a client that predates this field reports none, and the
board omits the bar (not a zero-slot bar) rather than guess a total. A rotating column,
not an append-only fact (``bzh:facts-not-status``'s one deliberate exception — the
registration row is already a mutable upsert; see ``hub/domain/registry.py``'s module
docstring): a re-registration (the runner's heartbeat) overwrites it in place, so a
``workspace_envs`` change converges on the next pull.

Idempotent like ``20260718_1130_hub_runner_token``: the column is added only where an
older database lacks it, so a fresh ``base -> head`` and an in-place upgrade both land at
exactly one column. ``op.add_column`` on the existing table — no table recreation, so no
frozen-literal schema copy is needed.

Revision ID: 20260718_1300_hub_runner_env_capacity
Revises: 20260718_1225_hub_chunk_migrations
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260718_1300_hub_runner_env_capacity"
down_revision: str | None = "20260718_1225_hub_chunk_migrations"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLE = "runner_registrations"
_COLUMN = "env_capacity"


def _has_column(bind: sa.Connection) -> bool:
    return _COLUMN in {c["name"] for c in sa.inspect(bind).get_columns(_TABLE)}


def upgrade() -> None:
    bind = op.get_bind()
    if not _has_column(bind):
        op.add_column(_TABLE, sa.Column(_COLUMN, sa.Integer(), nullable=True))


def downgrade() -> None:
    bind = op.get_bind()
    if _has_column(bind):
        op.drop_column(_TABLE, _COLUMN)

"""runner_hub_projects — the runner keeps the hub's latest answer to the projects it declares it serves.

A new ``hub_served_projects`` table holds that answer whole, one row per declared entry in declared order
(``position``, unique), with the project the entry resolved to — ``project_id``, ``slug`` and ``name`` NULL
for an entry no project answers to — and when the answer was stored. ``runner_identity`` gains nullable
``projects_answered_at``, which tells "no answer yet" apart from an answer serving nothing. Nothing is
backfilled: a carried runner shows its projects as not yet answered until its first registration after the
upgrade. Re-running skips a table or column already present. ``downgrade()`` drops both, losslessly — the
next registration rebuilds them after a later upgrade.
Revision ID: 20261008_1100_runner_hub_projects
Revises: 20261008_1000_runner_identity_tenant
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from blizzard.foundation.store.utc import UtcDateTime

revision: str = "20261008_1100_runner_hub_projects"
down_revision: str | None = "20261008_1000_runner_identity_tenant"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLE = "hub_served_projects"
_IDENTITY = "runner_identity"
_MARKER = "projects_answered_at"


def _identity_columns(bind: sa.Connection) -> set[str]:
    return {c["name"] for c in sa.inspect(bind).get_columns(_IDENTITY)}


def upgrade() -> None:
    bind = op.get_bind()
    if not sa.inspect(bind).has_table(_TABLE):
        op.create_table(
            _TABLE,
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column("position", sa.Integer(), nullable=False),
            sa.Column("entry", sa.String(), nullable=False),
            sa.Column("project_id", sa.String(), nullable=True),
            sa.Column("slug", sa.String(), nullable=True),
            sa.Column("name", sa.String(), nullable=True),
            sa.Column("answered_at", UtcDateTime(), nullable=False),
            sa.UniqueConstraint("position", name="uq_hub_served_projects_position"),
        )
    if _MARKER not in _identity_columns(bind):
        op.add_column(_IDENTITY, sa.Column(_MARKER, UtcDateTime(), nullable=True))


def downgrade() -> None:
    bind = op.get_bind()
    if _MARKER in _identity_columns(bind):
        with op.batch_alter_table(_IDENTITY) as batch:
            batch.drop_column(_MARKER)
    if sa.inspect(bind).has_table(_TABLE):
        op.drop_table(_TABLE)

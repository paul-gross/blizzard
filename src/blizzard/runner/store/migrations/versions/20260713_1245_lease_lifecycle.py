"""lease-lifecycle fact tables (runner store tree) — three append-only facts: each attempt's node
context, lease closures (an active lease has none), and binding releases (a held env has none).

Revision ID: 20260713_1245_runner_lease_lifecycle
Revises: 20260713_1218_runner_walking_skeleton
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from blizzard.foundation.store.utc import UtcDateTime
from blizzard.runner.store.schema import binding_releases, lease_closures

revision: str = "20260713_1245_runner_lease_lifecycle"
down_revision: str | None = "20260713_1218_runner_walking_skeleton"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# This revision's own frozen shape — no ``session_name``/``resolved_model``/
# ``resolved_effort`` columns, all added by a later revision (``bzh:frozen-revisions``).
_frozen_metadata = sa.MetaData()
_lease_context = sa.Table(
    "lease_context",
    _frozen_metadata,
    sa.Column("lease_id", sa.String, primary_key=True),
    sa.Column("chunk_id", sa.String, nullable=False),
    sa.Column("graph_id", sa.String, nullable=False),
    sa.Column("node_id", sa.String, nullable=False),
    sa.Column("node_name", sa.String, nullable=False),
    sa.Column("retries_max", sa.Integer, nullable=False),
    sa.Column("recorded_at", UtcDateTime, nullable=False),
)

_TABLES = [_lease_context, lease_closures, binding_releases]


def upgrade() -> None:
    bind = op.get_bind()
    for table in _TABLES:
        table.create(bind, checkfirst=False)


def downgrade() -> None:
    bind = op.get_bind()
    for table in reversed(_TABLES):
        table.drop(bind, checkfirst=False)

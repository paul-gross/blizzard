"""external usage samples — one append-only row per sampling attempt, ``payload`` NULL
when the attempt produced nothing (runner store tree, issue #218); ``slug`` is a later
reshape (blizzard#436), so this revision's own shape is a frozen local literal rather
than a ``schema.py`` import (``bzh:frozen-revisions``)

Revision ID: 20260801_1500_runner_external_usage_samples
Revises: 20260728_1500_runner_lease_session_stamps
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from blizzard.foundation.store.utc import UtcDateTime

revision: str = "20260801_1500_runner_external_usage_samples"
down_revision: str | None = "20260728_1500_runner_lease_session_stamps"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# This revision's own frozen shape — no `slug` column, added by
# 20260905_1000_runner_external_usage_samples_slug.
_frozen_metadata = sa.MetaData()
_external_usage_samples = sa.Table(
    "external_usage_samples",
    _frozen_metadata,
    sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
    sa.Column("sampled_at", UtcDateTime, nullable=False),
    sa.Column("payload", sa.Text, nullable=True),
)

_TABLES = (_external_usage_samples,)


def upgrade() -> None:
    bind = op.get_bind()
    for table in _TABLES:
        table.create(bind, checkfirst=True)


def downgrade() -> None:
    bind = op.get_bind()
    for table in reversed(_TABLES):
        table.drop(bind, checkfirst=True)

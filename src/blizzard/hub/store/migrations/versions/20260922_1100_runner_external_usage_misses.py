"""runner external subscription usage misses (blizzard#504 D7) — one refresh-in-place row per
``(runner_id, slug)``, a sibling to ``runner_external_usage``; a frozen local literal (``bzh:frozen-revisions``).

Revision ID: 20260922_1100_runner_external_usage_misses
Revises: 20260922_1000_review_findings
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from blizzard.foundation.store.utc import UtcDateTime

revision: str = "20260922_1100_runner_external_usage_misses"
down_revision: str | None = "20260922_1000_review_findings"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# This revision's own frozen shape — one row per (runner_id, slug), mirroring `runner_external_usage`.
_frozen_metadata = sa.MetaData()
_runner_external_usage_misses = sa.Table(
    "runner_external_usage_misses",
    _frozen_metadata,
    sa.Column("runner_id", sa.String, primary_key=True),
    sa.Column("slug", sa.String, primary_key=True),
    sa.Column("name", sa.String, nullable=False),
    sa.Column("missed_at", UtcDateTime, nullable=False),
    sa.Column("reason", sa.String, nullable=False),
    sa.Column("updated_at", UtcDateTime, nullable=False),
)

_TABLES = (_runner_external_usage_misses,)


def upgrade() -> None:
    bind = op.get_bind()
    for table in _TABLES:
        table.create(bind, checkfirst=True)


def downgrade() -> None:
    bind = op.get_bind()
    for table in reversed(_TABLES):
        table.drop(bind, checkfirst=True)

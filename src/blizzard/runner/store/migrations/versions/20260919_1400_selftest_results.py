"""Add the durable selftest-result table (blizzard#438).

Revision ID: 20260919_1400_selftest_results
Revises: 20260919_1000_transcript_segment_provenance
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

from blizzard.foundation.store.utc import UtcDateTime

revision = "20260919_1400_selftest_results"
down_revision = "20260919_1000_transcript_segment_provenance"
branch_labels = None
depends_on = None

_RESULTS = "selftest_results"


def upgrade() -> None:
    bind = op.get_bind()
    existing = sa.inspect(bind).get_table_names()
    if _RESULTS not in existing:
        op.create_table(
            _RESULTS,
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column("harness_id", sa.String(), nullable=False),
            sa.Column("status", sa.String(), nullable=False),
            sa.Column("error", sa.Text(), nullable=True),
            sa.Column("recorded_at", UtcDateTime(), nullable=False),
        )


def downgrade() -> None:
    bind = op.get_bind()
    existing = sa.inspect(bind).get_table_names()
    if _RESULTS in existing:
        op.drop_table(_RESULTS)

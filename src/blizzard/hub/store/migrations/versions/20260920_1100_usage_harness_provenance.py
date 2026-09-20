"""Harness id/version onto usage_facts.

Two guarded, nullable columns — un-backfilled, so NULL declares unknown, never a value
(blizzard#441).
Revision ID: 20260920_1100_hub_usage_harness_provenance
Revises: 20260919_1000_hub_transcript_provenance
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260920_1100_hub_usage_harness_provenance"
down_revision: str | None = "20260919_1000_hub_transcript_provenance"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_USAGE_FACTS = "usage_facts"
_COLUMNS = ("harness_id", "harness_version")


def _columns(bind: sa.Connection, table: str) -> set[str]:
    return {c["name"] for c in sa.inspect(bind).get_columns(table)}


def upgrade() -> None:
    bind = op.get_bind()
    existing = _columns(bind, _USAGE_FACTS)
    for name in _COLUMNS:
        if name not in existing:
            op.add_column(_USAGE_FACTS, sa.Column(name, sa.String(), nullable=True))


def downgrade() -> None:
    bind = op.get_bind()
    existing = _columns(bind, _USAGE_FACTS)
    for name in _COLUMNS:
        if name in existing:
            with op.batch_alter_table(_USAGE_FACTS) as batch:
                batch.drop_column(name)

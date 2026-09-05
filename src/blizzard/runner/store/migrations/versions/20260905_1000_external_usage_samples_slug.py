"""external usage samples' subscription join key — ``slug``, backfilled to the legacy
Anthropic slug for every row a runner recorded before ``[[subscription]]`` declarations
existed (runner store tree, blizzard#436)

Revision ID: 20260905_1000_runner_external_usage_samples_slug
Revises: 20260831_1000_runner_in_flight_elicitations
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260905_1000_runner_external_usage_samples_slug"
down_revision: str | None = "20260831_1000_runner_in_flight_elicitations"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLE = "external_usage_samples"
# Mirrors `blizzard.runner.config.LEGACY_ANTHROPIC_SLUG` — restated rather than imported
# (``bzh:frozen-revisions``): this backfill value is historical, fixed at the instant this
# revision ran, and must never track whatever that constant says today.
_LEGACY_ANTHROPIC_SLUG = "anthropic"

# The reshaped shape — the one column the backfill's UPDATE names, added by this revision.
_EXTERNAL_USAGE_SAMPLES = sa.Table(
    "external_usage_samples",
    sa.MetaData(),
    sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
    sa.Column("slug", sa.String, nullable=True),
)


def _has_slug(bind: sa.Connection) -> bool:
    return "slug" in {c["name"] for c in sa.inspect(bind).get_columns(_TABLE)}


def upgrade() -> None:
    bind = op.get_bind()
    if _has_slug(bind):
        return  # already reshaped — this revision's own guard, not per-row

    with op.batch_alter_table(_TABLE) as batch:
        batch.add_column(sa.Column("slug", sa.String, nullable=True))

    bind.execute(_EXTERNAL_USAGE_SAMPLES.update().values(slug=_LEGACY_ANTHROPIC_SLUG))

    with op.batch_alter_table(_TABLE) as batch:
        batch.alter_column("slug", nullable=False)


def downgrade() -> None:
    bind = op.get_bind()
    if not _has_slug(bind):
        return  # already the pre-reshape shape

    with op.batch_alter_table(_TABLE) as batch:
        batch.drop_column("slug")

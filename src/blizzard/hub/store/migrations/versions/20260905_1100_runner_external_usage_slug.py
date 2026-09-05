"""runner_external_usage's subscription join key (blizzard#436 phase 3) — widens the
primary key to ``(runner_id, slug)``, backfilling every existing row to the legacy slug/name.

Revision ID: 20260905_1100_hub_runner_external_usage_slug
Revises: 20260902_1000_finding_facts_ref
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260905_1100_hub_runner_external_usage_slug"
down_revision: str | None = "20260902_1000_finding_facts_ref"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLE = "runner_external_usage"
_PK_NAME = "pk_runner_external_usage"

# Mirrors `blizzard.wire.facts.LEGACY_ANTHROPIC_SLUG` — restated, not imported
# (``bzh:frozen-revisions``): this historical backfill value must never track it.
_LEGACY_ANTHROPIC_SLUG = "anthropic"
_LEGACY_ANTHROPIC_NAME = "Anthropic"

# The reshaped shape — the two columns the backfill's UPDATE names, added by this revision.
_RUNNER_EXTERNAL_USAGE = sa.Table(
    "runner_external_usage",
    sa.MetaData(),
    sa.Column("runner_id", sa.String, primary_key=True),
    sa.Column("slug", sa.String, nullable=True),
    sa.Column("name", sa.String, nullable=True),
)


def _has_slug(bind: sa.Connection) -> bool:
    return "slug" in {c["name"] for c in sa.inspect(bind).get_columns(_TABLE)}


def upgrade() -> None:
    bind = op.get_bind()
    if _has_slug(bind):
        return  # already reshaped — this revision's own guard, not per-row

    with op.batch_alter_table(_TABLE) as batch:
        batch.add_column(sa.Column("slug", sa.String, nullable=True))
        batch.add_column(sa.Column("name", sa.String, nullable=True))

    bind.execute(
        _RUNNER_EXTERNAL_USAGE.update().values(slug=_LEGACY_ANTHROPIC_SLUG, name=_LEGACY_ANTHROPIC_NAME)
    )

    # `recreate="always"` is dialect-agnostic (``bzh:sql-portable``): one copy-based
    # rebuild widens the primary key on both sqlite and postgres, no dialect branch.
    with op.batch_alter_table(_TABLE, recreate="always") as batch:
        batch.alter_column("slug", nullable=False)
        batch.alter_column("name", nullable=False)
        batch.create_primary_key(_PK_NAME, ["runner_id", "slug"])


def downgrade() -> None:
    bind = op.get_bind()
    if not _has_slug(bind):
        return  # already the pre-reshape shape

    # Lossy: a multi-subscription runner_id keeps only its legacy-slug row, the pre-reshape
    # shape's one-row-per-runner_id (mirrors 20260716_2206's accepted duplicate-drop).
    bind.execute(_RUNNER_EXTERNAL_USAGE.delete().where(_RUNNER_EXTERNAL_USAGE.c.slug != _LEGACY_ANTHROPIC_SLUG))

    with op.batch_alter_table(_TABLE, recreate="always") as batch:
        batch.drop_column("slug")
        batch.drop_column("name")
        batch.create_primary_key(_PK_NAME, ["runner_id"])

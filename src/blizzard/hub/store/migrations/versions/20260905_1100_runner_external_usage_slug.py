"""runner external usage's subscription join key — ``slug`` and its operator-facing
``name``, widening the primary key to ``(runner_id, slug)`` (hub store tree, blizzard#436
phase 3). Every existing row is backfilled to the legacy Anthropic slug/name: it was
one-per-``runner_id`` before this reshape, so backfilling ``slug`` and widening the
primary key to a strict superset key cannot violate uniqueness.

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

# Mirrors `blizzard.runner.config.LEGACY_ANTHROPIC_SLUG`/its synthesized declaration's
# `name` — restated rather than imported (``bzh:frozen-revisions``, ``bzh:domain-core``):
# this backfill value is historical, fixed at the instant this revision ran, and must
# never track whatever those constants say today.
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

    # `recreate="always"` is dialect-agnostic (``bzh:sql-portable``): the same copy-based
    # rebuild widens the primary key on sqlite (which has no in-place ``ALTER`` for it)
    # and on postgres alike, with no dialect branch in this script.
    with op.batch_alter_table(_TABLE, recreate="always") as batch:
        batch.alter_column("slug", nullable=False)
        batch.alter_column("name", nullable=False)
        batch.create_primary_key(_PK_NAME, ["runner_id", "slug"])


def downgrade() -> None:
    bind = op.get_bind()
    if not _has_slug(bind):
        return  # already the pre-reshape shape

    # Lossy: a runner_id with more than one slug's row keeps only its legacy-slug row
    # (mirrors 20260716_2206's accepted duplicate-drop) — the pre-reshape shape had
    # exactly one row per runner_id, so a multi-subscription runner can't downgrade
    # without picking one.
    bind.execute(_RUNNER_EXTERNAL_USAGE.delete().where(_RUNNER_EXTERNAL_USAGE.c.slug != _LEGACY_ANTHROPIC_SLUG))

    with op.batch_alter_table(_TABLE, recreate="always") as batch:
        batch.drop_column("slug")
        batch.drop_column("name")
        batch.create_primary_key(_PK_NAME, ["runner_id"])

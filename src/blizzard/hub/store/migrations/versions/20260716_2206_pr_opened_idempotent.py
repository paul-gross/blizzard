"""``pr.opened`` idempotent per (chunk, repo) — a DB-level close of a write race (issue #10)

Adds a unique constraint on (chunk_id, repo), keeping the earliest row of each duplicate pair first.
Revision ID: 20260716_2206_hub_pr_opened_idempotent
Revises: 20260716_1512_hub_pm_pointer_source_ref
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260716_2206_hub_pr_opened_idempotent"
down_revision: str | None = "20260716_1512_hub_pm_pointer_source_ref"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_UNIQUE_NAME = "uq_delivery_pr_opened_chunk_repo"

_PR_OPENED = sa.Table(
    "delivery_pr_opened",
    sa.MetaData(),
    sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
    sa.Column("chunk_id", sa.String, nullable=False),
    sa.Column("repo", sa.String, nullable=False),
    sa.Column("pr_number", sa.Integer, nullable=False),
    sa.Column("pr_url", sa.String, nullable=False),
    sa.Column("commit_hash", sa.String, nullable=False),
    sa.Column("opened_at", sa.DateTime, nullable=False),
)


def upgrade() -> None:
    bind = op.get_bind()

    # Keep the earliest row (lowest id) per (chunk_id, repo); drop the rest — the
    # duplicates the race produced (see module docstring).
    rows = bind.execute(sa.select(_PR_OPENED.c.id, _PR_OPENED.c.chunk_id, _PR_OPENED.c.repo)).all()
    keep: dict[tuple[str, str], int] = {}
    for row in rows:
        key = (row.chunk_id, row.repo)
        if key not in keep or row.id < keep[key]:
            keep[key] = row.id
    doomed = [row.id for row in rows if row.id != keep[(row.chunk_id, row.repo)]]
    if doomed:
        bind.execute(_PR_OPENED.delete().where(_PR_OPENED.c.id.in_(doomed)))

    with op.batch_alter_table("delivery_pr_opened") as batch:
        batch.create_unique_constraint(_UNIQUE_NAME, ["chunk_id", "repo"])


def downgrade() -> None:
    # Schema-reversing only: the duplicates upgrade() removed are gone for good — the
    # accepted, recorded cost of closing the race (mirrors 0013's lossy-owner downgrade).
    with op.batch_alter_table("delivery_pr_opened") as batch:
        batch.drop_constraint(_UNIQUE_NAME, type_="unique")

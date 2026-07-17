"""``pr.opened`` idempotent per (chunk, repo) — a DB-level close of a write race (issue #10)

The open-pr deliver mode's coordinator runs on both a fresh apply and an
idempotent completion replay — deliberate, so a mid-delivery crash resumes
rather than wedges the chunk (``blizzard.hub.delivery.coordinator``). Its DB-backed
``open_prs`` skip-set (a store read each call, not an in-memory cache) has a narrow
read-then-write race between two overlapping runs, so a dogfood run accumulated two
``delivery_pr_opened`` rows for the same (chunk, repo)
— the board double-listed one PR. Harmless in effect (the forge's ``open_pr`` reuses an
existing PR for the head; GitHub has one PR), but the write itself should not be able
to produce a second row.

This revision adds ``uq_delivery_pr_opened_chunk_repo`` — a unique constraint on
(chunk_id, repo) — so the race can no longer land a duplicate; the store adapter
(``ChunkStore.record_pr_opened``) now catches the collision and discards it as the
harmless duplicate write it is, the same CAS shape ``question_answers`` already uses.

**De-duplicate before constraining:** a store carrying the dogfood duplicates would
fail this revision's constraint add outright, so ``upgrade()`` first deletes every
row but the earliest (lowest ``id``) per (chunk_id, repo) — the accepted duplicate the
coordinator would have kept anyway (the first write wins; the race only ever produces a
second, redundant fact, never a conflicting one, since both writers observe the same
forge-assigned PR).

Local ``sa.Table`` literals, not an import of ``blizzard.hub.store.schema`` (the reason
recorded in ``0013_pm_pointer_source_ref``'s docstring): a migration's meaning must not
depend on when it is read, and ``schema.py`` now carries this very constraint.

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

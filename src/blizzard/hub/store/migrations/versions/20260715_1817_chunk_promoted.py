"""chunk readiness — the not-ready resting state and its promotion (hub store tree).
Back-fills a ``chunk.promoted`` row at each chunk's own ``minted_at``, idempotently.

Revision ID: 20260715_1817_hub_chunk_promoted
Revises: 20260714_0819_hub_delivery_pr_facts
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy import insert, select

from blizzard.foundation.store.utc import UtcDateTime
from blizzard.hub.store.schema import chunk_promoted

revision: str = "20260715_1817_hub_chunk_promoted"
down_revision: str | None = "20260714_0819_hub_delivery_pr_facts"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# A read-only reference, not a create — this revision only selects from ``chunks``, so the
# frozen shape is the narrow stub of columns the query names, not a full literal
# (``bzh:frozen-revisions``). Never created, never dropped.
_frozen_metadata = sa.MetaData()
chunks = sa.Table(
    "chunks",
    _frozen_metadata,
    sa.Column("chunk_id", sa.String, primary_key=True),
    sa.Column("minted_at", UtcDateTime, nullable=False),
)


def upgrade() -> None:
    bind = op.get_bind()
    chunk_promoted.create(bind, checkfirst=True)
    # Back-fill every pre-existing chunk so it stays claimable. Idempotent: a
    # chunk already promoted (e.g. on a re-run) is skipped.
    already = {r.chunk_id for r in bind.execute(select(chunk_promoted.c.chunk_id))}
    rows = [
        {"chunk_id": r.chunk_id, "promoted_at": r.minted_at}
        for r in bind.execute(select(chunks.c.chunk_id, chunks.c.minted_at))
        if r.chunk_id not in already
    ]
    if rows:
        bind.execute(insert(chunk_promoted), rows)


def downgrade() -> None:
    bind = op.get_bind()
    chunk_promoted.drop(bind, checkfirst=True)

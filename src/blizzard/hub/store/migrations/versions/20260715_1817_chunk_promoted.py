"""chunk readiness — the not-ready resting state and its promotion (hub store tree).
Back-fills a ``chunk.promoted`` row at each chunk's own ``minted_at``, idempotently.

Revision ID: 20260715_1817_hub_chunk_promoted
Revises: 20260714_0819_hub_delivery_pr_facts
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op
from sqlalchemy import insert, select

from blizzard.hub.store.schema import chunk_promoted, chunks

revision: str = "20260715_1817_hub_chunk_promoted"
down_revision: str | None = "20260714_0819_hub_delivery_pr_facts"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


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

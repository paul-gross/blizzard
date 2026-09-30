"""Artifacts' durable write order — ``seq`` on ``artifacts``, a per-chunk counter. The
backfill assigns 1..n per chunk by ``(epoch, produced_at, artifact_id)``, the best
order the pre-``seq`` rows carry.

Revision ID: 20260930_1000_artifact_seq
Revises: 20260929_1100_drop_open_pr_facts
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from blizzard.foundation.store.utc import UtcDateTime

revision: str = "20260930_1000_artifact_seq"
down_revision: str | None = "20260929_1100_drop_open_pr_facts"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_ARTIFACTS = sa.Table(
    "artifacts",
    sa.MetaData(),
    sa.Column("artifact_id", sa.String, primary_key=True),
    sa.Column("chunk_id", sa.String, nullable=False),
    sa.Column("epoch", sa.Integer, nullable=False),
    sa.Column("produced_at", UtcDateTime, nullable=False),
    sa.Column("seq", sa.Integer, nullable=True),
)


def upgrade() -> None:
    bind = op.get_bind()
    with op.batch_alter_table("artifacts") as batch:
        batch.add_column(sa.Column("seq", sa.Integer, nullable=True))

    rows = bind.execute(
        sa.select(_ARTIFACTS.c.artifact_id, _ARTIFACTS.c.chunk_id).order_by(
            _ARTIFACTS.c.chunk_id, _ARTIFACTS.c.epoch, _ARTIFACTS.c.produced_at, _ARTIFACTS.c.artifact_id
        )
    ).all()
    next_seq: dict[str, int] = defaultdict(lambda: 1)
    for row in rows:
        bind.execute(
            _ARTIFACTS.update().where(_ARTIFACTS.c.artifact_id == row.artifact_id).values(seq=next_seq[row.chunk_id])
        )
        next_seq[row.chunk_id] += 1

    with op.batch_alter_table("artifacts") as batch:
        batch.alter_column("seq", existing_type=sa.Integer, nullable=False)


def downgrade() -> None:
    with op.batch_alter_table("artifacts") as batch:
        batch.drop_column("seq")

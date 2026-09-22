"""Adds review-sourced findings (blizzard#582 D1): `findings` gains `source`,
`severity`, and `raised_by_chunk_id`; `routine_name` becomes nullable.

Revision ID: 20260922_1000_review_findings
Revises: 20260920_1200_finding_delivered_state
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260922_1000_review_findings"
down_revision: str | None = "20260920_1200_finding_delivered_state"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_FINDINGS_TABLE = "findings"
_CHECK_NAME = "ck_findings_source"
_INDEX_NAME = "ix_findings_scope_source"

# Narrow read/write stub: downgrade backfills only this column.
_findings = sa.Table("findings", sa.MetaData(), sa.Column("routine_name", sa.String))


def upgrade() -> None:
    # SQLite has no ALTER for column nullability or a new FK, so this table-copy
    # recreate is load-bearing (`blizzard-context:/standards/persistence.md`).
    with op.batch_alter_table(_FINDINGS_TABLE) as batch:
        batch.alter_column("routine_name", existing_type=sa.String(), nullable=True)
        batch.add_column(sa.Column("source", sa.String(), nullable=False, server_default="routine"))
        batch.add_column(sa.Column("severity", sa.String(), nullable=True))
        batch.add_column(
            sa.Column(
                "raised_by_chunk_id",
                sa.String(),
                sa.ForeignKey("chunks.chunk_id", name="fk_findings_raised_by_chunk_id"),
                nullable=True,
            )
        )
        batch.create_check_constraint(_CHECK_NAME, "source IN ('routine', 'review')")
    op.create_index(_INDEX_NAME, _FINDINGS_TABLE, ["scope_slug", "source"])


def downgrade() -> None:
    op.drop_index(_INDEX_NAME, table_name=_FINDINGS_TABLE)
    bind = op.get_bind()
    # A review-sourced row's null `routine_name` has no home in the old `NOT NULL`
    # shape, so it is coalesced to `""` before the column narrows.
    bind.execute(_findings.update().where(_findings.c.routine_name.is_(None)).values(routine_name=""))
    with op.batch_alter_table(_FINDINGS_TABLE) as batch:
        batch.drop_constraint(_CHECK_NAME, type_="check")
        batch.drop_column("raised_by_chunk_id")
        batch.drop_column("severity")
        batch.drop_column("source")
        batch.alter_column("routine_name", existing_type=sa.String(), nullable=False)

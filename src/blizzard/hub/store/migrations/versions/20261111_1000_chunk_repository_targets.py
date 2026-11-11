"""chunk repository targets — the branch each chunk captured for each repository its project links.

Creates `chunk_repository_targets`, tenant-keyed, unique on `(tenant_id, chunk_id, repository_id)`, with no backfill:
a chunk claimed before this revision captures each repository at its next delivery. The upgrade skips a table a stopped
run already made. The downgrade drops the table, and with it every captured target.
Revision ID: 20261111_1000_chunk_repository_targets
Revises: 20261111_0900_repository_link_base_branch
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from blizzard.foundation.store.utc import UtcDateTime

revision: str = "20261111_1000_chunk_repository_targets"
down_revision: str | None = "20261111_0900_repository_link_base_branch"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLE = "chunk_repository_targets"


def upgrade() -> None:
    if sa.inspect(op.get_bind()).has_table(_TABLE):
        return
    op.create_table(
        _TABLE,
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("chunk_id", sa.String(), nullable=False),
        sa.Column("repository_id", sa.String(), nullable=False),
        sa.Column("base_branch", sa.String(), nullable=False),
        sa.Column("inherited", sa.Boolean(), nullable=False),
        sa.Column("captured_at", UtcDateTime(), nullable=False),
        sa.Column("tenant_id", sa.String(), nullable=False),
        sa.ForeignKeyConstraint(["chunk_id"], ["chunks.chunk_id"]),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.tenant_id"], name=f"fk_{_TABLE}_tenant_id"),
        sa.UniqueConstraint("tenant_id", "chunk_id", "repository_id", name=f"uq_{_TABLE}_chunk_repository"),
    )


def downgrade() -> None:
    if sa.inspect(op.get_bind()).has_table(_TABLE):
        op.drop_table(_TABLE)

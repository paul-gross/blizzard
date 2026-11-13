"""Repair repository target schema missing from stores stamped at the CI revision.

Revision ID: 20261112_0900_repair_repository_targets
Revises: 20261111_0900_allow_no_ci
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from blizzard.foundation.store.utc import UtcDateTime

revision: str = "20261112_0900_repair_repository_targets"
down_revision: str | None = "20261111_0900_allow_no_ci"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if "base_branch" not in {c["name"] for c in inspector.get_columns("project_repository_links")}:
        op.add_column("project_repository_links", sa.Column("base_branch", sa.String(), nullable=True))
    if not inspector.has_table("chunk_repository_targets"):
        op.create_table(
            "chunk_repository_targets",
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column("chunk_id", sa.String(), nullable=False),
            sa.Column("repository_id", sa.String(), nullable=False),
            sa.Column("base_branch", sa.String(), nullable=False),
            sa.Column("inherited", sa.Boolean(), nullable=False),
            sa.Column("captured_at", UtcDateTime(), nullable=False),
            sa.Column("tenant_id", sa.String(), nullable=False),
            sa.ForeignKeyConstraint(["chunk_id"], ["chunks.chunk_id"]),
            sa.ForeignKeyConstraint(["tenant_id"], ["tenants.tenant_id"], name="fk_chunk_repository_targets_tenant_id"),
            sa.UniqueConstraint(
                "tenant_id", "chunk_id", "repository_id", name="uq_chunk_repository_targets_chunk_repository"
            ),
        )


def downgrade() -> None:
    """Keep the schema and data: the parent revision already owns both additions."""
    return None

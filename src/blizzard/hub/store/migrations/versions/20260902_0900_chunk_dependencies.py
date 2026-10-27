"""chunk_dependencies — the declared dependent-on-prerequisite edge. One
new table.

Revision ID: 20260902_0900_chunk_dependencies
Revises: 20260901_0900_finding_facts_finding_set_id
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from blizzard.foundation.store.utc import UtcDateTime

revision: str = "20260902_0900_chunk_dependencies"
down_revision: str | None = "20260901_0900_finding_facts_finding_set_id"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Frozen (``bzh:frozen-revisions``); ``chunks`` is an FK stub.
_frozen_metadata = sa.MetaData()
sa.Table(
    "chunks",
    _frozen_metadata,
    sa.Column("chunk_id", sa.String, primary_key=True),
)
_chunk_dependencies = sa.Table(
    "chunk_dependencies",
    _frozen_metadata,
    sa.Column("dependency_id", sa.String, primary_key=True),
    sa.Column("dependent_chunk_id", sa.String, sa.ForeignKey("chunks.chunk_id"), nullable=False),
    sa.Column("prerequisite_chunk_id", sa.String, sa.ForeignKey("chunks.chunk_id"), nullable=False),
    sa.Column("declared_at", UtcDateTime, nullable=False),
    sa.Column("declared_by", sa.String, nullable=False),
    sa.Column("released_at", UtcDateTime, nullable=True),
    sa.Column("released_by", sa.String, nullable=True),
)
sa.Index("ix_chunk_dependencies_dependent_chunk_id", _chunk_dependencies.c.dependent_chunk_id)
sa.Index("ix_chunk_dependencies_prerequisite_chunk_id", _chunk_dependencies.c.prerequisite_chunk_id)

_TABLES = (_chunk_dependencies,)


def upgrade() -> None:
    bind = op.get_bind()
    for table in _TABLES:
        table.create(bind, checkfirst=True)


def downgrade() -> None:
    bind = op.get_bind()
    for table in reversed(_TABLES):
        table.drop(bind, checkfirst=True)

"""garden_proposals.source_artifact_id, garden_proposals.ref — a delivered proposal's
idempotence key (blizzard#393). Both nullable, no backfill, no `ForeignKey` (SQLite
cannot ALTER-add a constrained column).

Revision ID: 20260830_2015_garden_proposals_source_artifact
Revises: 20260830_1835_work_item_runs
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260830_2015_garden_proposals_source_artifact"
down_revision: str | None = "20260830_1835_work_item_runs"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLE = "garden_proposals"
_SOURCE_ARTIFACT_COLUMN = "source_artifact_id"
_REF_COLUMN = "ref"
_INDEX = "ux_garden_proposals_source_artifact_ref"


def _existing_columns(bind: sa.Connection) -> set[str]:
    return {c["name"] for c in sa.inspect(bind).get_columns(_TABLE)}


def _existing_indexes(bind: sa.Connection) -> set[str | None]:
    return {ix["name"] for ix in sa.inspect(bind).get_indexes(_TABLE)}


def upgrade() -> None:
    bind = op.get_bind()
    columns = _existing_columns(bind)
    if _SOURCE_ARTIFACT_COLUMN not in columns:
        op.add_column(_TABLE, sa.Column(_SOURCE_ARTIFACT_COLUMN, sa.String(), nullable=True))
    if _REF_COLUMN not in columns:
        op.add_column(_TABLE, sa.Column(_REF_COLUMN, sa.String(), nullable=True))
    if _INDEX not in _existing_indexes(bind):
        op.create_index(_INDEX, _TABLE, [_SOURCE_ARTIFACT_COLUMN, _REF_COLUMN], unique=True)


def downgrade() -> None:
    bind = op.get_bind()
    if _INDEX in _existing_indexes(bind):
        op.drop_index(_INDEX, table_name=_TABLE)
    columns = _existing_columns(bind)
    if _REF_COLUMN in columns:
        op.drop_column(_TABLE, _REF_COLUMN)
    if _SOURCE_ARTIFACT_COLUMN in columns:
        op.drop_column(_TABLE, _SOURCE_ARTIFACT_COLUMN)

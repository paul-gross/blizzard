"""forge_annotation_facts — the work sources this hub annotates, remembered across restarts.

Revision ID: 20261004_1400_forge_annotation_facts
Revises: 20261004_1300_egress_events
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from blizzard.foundation.store.utc import UtcDateTime

revision: str = "20261004_1400_forge_annotation_facts"
down_revision: str | None = "20261004_1300_egress_events"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLE = "forge_annotation_facts"
_INDEX = "ix_forge_annotation_facts_source_name_id"


def upgrade() -> None:
    bind = op.get_bind()
    if _TABLE not in sa.inspect(bind).get_table_names():
        op.create_table(
            _TABLE,
            sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
            sa.Column("source_name", sa.String, nullable=False),
            sa.Column("annotating", sa.Boolean, nullable=False),
            sa.Column("recorded_at", UtcDateTime, nullable=False),
        )
    if _INDEX not in {str(i["name"]) for i in sa.inspect(bind).get_indexes(_TABLE)}:
        op.create_index(_INDEX, _TABLE, ["source_name", "id"])


def downgrade() -> None:
    if _TABLE in sa.inspect(op.get_bind()).get_table_names():
        op.drop_table(_TABLE)

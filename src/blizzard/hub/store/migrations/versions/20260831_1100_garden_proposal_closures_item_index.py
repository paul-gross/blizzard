"""garden_proposal_closures(source, ref) index (blizzard#394 Phase 3) — the reverse read a
delivered item's own pointer needs: which accepted proposal, if any, minted it.

Revision ID: 20260831_1100_gpc_item_index
Revises: 20260831_1030_finding_exits
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260831_1100_gpc_item_index"
down_revision: str | None = "20260831_1030_finding_exits"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLE = "garden_proposal_closures"
_COLUMNS = ["source", "ref"]
_INDEX = "ix_garden_proposal_closures_source_ref"


def _has_index(bind: sa.Connection) -> bool:
    return _INDEX in {i["name"] for i in sa.inspect(bind).get_indexes(_TABLE)}


def upgrade() -> None:
    bind = op.get_bind()
    if not _has_index(bind):
        op.create_index(_INDEX, _TABLE, _COLUMNS)


def downgrade() -> None:
    bind = op.get_bind()
    if _has_index(bind):
        op.drop_index(_INDEX, table_name=_TABLE)

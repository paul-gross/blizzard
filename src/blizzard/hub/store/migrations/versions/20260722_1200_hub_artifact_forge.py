"""artifacts.forge — the worker's declared origin, on the git_commit artifact row (issue #143 Phase 4)

A nullable ``git_commit``-only sibling of ``repo``; null reads as "the repo's origin".
Revision ID: 20260722_1200_hub_artifact_forge
Revises: 20260721_1600_hub_event_log
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260722_1200_hub_artifact_forge"
down_revision: str | None = "20260721_1600_hub_event_log"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLE = "artifacts"
_COLUMN = "forge"


def _has_column(bind: sa.Connection) -> bool:
    return _COLUMN in {c["name"] for c in sa.inspect(bind).get_columns(_TABLE)}


def upgrade() -> None:
    bind = op.get_bind()
    if not _has_column(bind):
        op.add_column(_TABLE, sa.Column(_COLUMN, sa.String(), nullable=True))


def downgrade() -> None:
    bind = op.get_bind()
    if _has_column(bind):
        op.drop_column(_TABLE, _COLUMN)

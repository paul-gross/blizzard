"""chunk model selection — a mutable ``model`` column on chunks, added only where an
older database lacks it (issue #27, hub store tree)

Revision ID: 20260717_2318_hub_chunk_model_selection
Revises: 20260717_0446_hub_chunk_pause_facts
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260717_2318_hub_chunk_model_selection"
down_revision: str | None = "20260717_0446_hub_chunk_pause_facts"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLE = "chunks"
_COLUMN = "model"

# The model every chunk ran on before this column existed — see the module docstring.
_DEFAULT_MODEL = "claude-opus-4-8"


def _has_column(bind: sa.Connection) -> bool:
    return _COLUMN in {c["name"] for c in sa.inspect(bind).get_columns(_TABLE)}


def upgrade() -> None:
    bind = op.get_bind()
    if not _has_column(bind):
        op.add_column(_TABLE, sa.Column(_COLUMN, sa.String(), nullable=False, server_default=_DEFAULT_MODEL))


def downgrade() -> None:
    bind = op.get_bind()
    if _has_column(bind):
        op.drop_column(_TABLE, _COLUMN)

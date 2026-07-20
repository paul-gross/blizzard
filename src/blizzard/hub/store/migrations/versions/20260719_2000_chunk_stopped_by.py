"""chunk_stopped.stopped_by — who terminally stopped the chunk (hub store tree)

Issue #118 gives the operator a ``blizzard hub stop`` verb over the previously
write-only ``chunk_stopped`` fact. This revision adds the column its ``--by`` lands
in, mirroring ``chunk_pause_facts.set_by``.

Nullable: a stopped row written before this column existed — including the hand-
written ``INSERT INTO chunk_stopped`` the issue's motivating incident required —
predates it and reads back as ``None``, the same tolerance
``20260718_0930_hub_runner_local_pause_reason`` gives ``reason``.

Idempotent like that revision: the column is added only where an older database
lacks it, so a fresh ``base -> head`` and an in-place upgrade both land at exactly
one column.

Revision ID: 20260719_2000_hub_chunk_stopped_by
Revises: 20260718_1300_hub_runner_env_capacity
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260719_2000_hub_chunk_stopped_by"
down_revision: str | None = "20260719_0900_hub_graph_lifecycle_facts"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLE = "chunk_stopped"
_COLUMN = "stopped_by"


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

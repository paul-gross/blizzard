"""chunk default model/effort — additive and nullable, with no backfill, since NULL is
exactly "expresses no preference"; ``chunks.model`` is retained and unread (issue #144)

Revision ID: 20260728_1410_hub_chunk_defaults
Revises: 20260728_1400_hub_graph_sessions
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260728_1410_hub_chunk_defaults"
down_revision: str | None = "20260728_1400_hub_graph_sessions"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLE = "chunks"
# Name-and-type pairs rather than `Column` objects: a `Column` is bound to the table it is
# first attached to, so reusing one across `upgrade`/`downgrade` needs a deprecated `copy()`.
_COLUMNS: tuple[tuple[str, sa.types.TypeEngine[str]], ...] = (
    ("default_model", sa.Text()),
    ("default_effort", sa.String()),
)


def _columns(bind: sa.Connection) -> set[str]:
    return {c["name"] for c in sa.inspect(bind).get_columns(_TABLE)}


def upgrade() -> None:
    bind = op.get_bind()
    present = _columns(bind)
    for name, type_ in _COLUMNS:
        if name not in present:
            op.add_column(_TABLE, sa.Column(name, type_, nullable=True))


def downgrade() -> None:
    bind = op.get_bind()
    present = _columns(bind)
    with op.batch_alter_table(_TABLE) as batch:
        for name, _type in _COLUMNS:
            if name in present:
                batch.drop_column(name)

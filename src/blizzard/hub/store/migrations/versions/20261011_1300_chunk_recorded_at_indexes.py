"""chunk recorded-at indexes — the superseded-terminal check searches one chunk's rows, not the whole tenant's.

``(tenant_id, chunk_id, recorded_at)`` on the transition, migration and restart tables bounds a newer-row search to
one chunk's rows over ``recorded_at`` (``src/blizzard/hub/store/internal/chunk_terminal_predicates.py``). Each index
is inspected first, so a re-run changes nothing.

The downgrade drops the three indexes.
Revision ID: 20261011_1300_chunk_recorded_at_indexes
Revises: 20261008_1900_project_columns
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20261011_1300_chunk_recorded_at_indexes"
down_revision: str | None = "20261008_1900_project_columns"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

#: The indexes this revision adds: name, table, columns.
ADDED_INDEXES: tuple[tuple[str, str, tuple[str, ...]], ...] = (
    ("ix_transitions_tenant_chunk_id_recorded_at", "transitions", ("tenant_id", "chunk_id", "recorded_at")),
    ("ix_chunk_migrations_tenant_chunk_id_recorded_at", "chunk_migrations", ("tenant_id", "chunk_id", "recorded_at")),
    ("ix_chunk_restarts_tenant_chunk_id_recorded_at", "chunk_restarts", ("tenant_id", "chunk_id", "recorded_at")),
)


def _index_names(bind: sa.Connection, table: str) -> set[str]:
    return {str(i["name"]) for i in sa.inspect(bind).get_indexes(table)}


def upgrade() -> None:
    bind = op.get_bind()
    for name, table, columns in ADDED_INDEXES:
        if name not in _index_names(bind, table):
            op.create_index(name, table, list(columns))


def downgrade() -> None:
    bind = op.get_bind()
    for name, table, _columns in ADDED_INDEXES:
        if name in _index_names(bind, table):
            op.drop_index(name, table_name=table)

"""chunk_id indexes on the per-chunk fact tables (blizzard#421).

``ChunkStore.load_facts`` and ``_route_of_conn`` filter twenty-one fact tables by
``chunk_id``, none of which carried an index or a unique constraint leading with it — every
one of those filters was a sequential scan. ``delivery_pr_opened`` is excluded: its
``uq_delivery_pr_opened_chunk_repo`` unique constraint already leads with ``chunk_id``, so an
index here would be redundant write cost for no read.

Revision ID: 20260829_1930_fact_tables_chunk_id_index
Revises: 20260829_0900_findings_and_proposals
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260829_1930_fact_tables_chunk_id_index"
down_revision: str | None = "20260829_0900_findings_and_proposals"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_COLUMN = "chunk_id"

_TABLES = (
    "transitions",
    "chunk_migrations",
    "chunk_restarts",
    "lease_facts",
    "escalations",
    "route_created",
    "route_released",
    "route_token_minted",
    "questions",
    "decisions",
    "requeues",
    "chunk_pause_facts",
    "usage_facts",
    "delivery_repo_landed",
    "chunk_bounces",
    "hub_node_poll",
    "chunk_stopped",
    "chunk_completed",
    "delivery_pr_closed",
    "chunk_promoted",
    "delivery_landed",
)


def _index_name(table: str) -> str:
    return f"ix_{table}_{_COLUMN}"


def _has_index(bind: sa.Connection, table: str) -> bool:
    return _index_name(table) in {i["name"] for i in sa.inspect(bind).get_indexes(table)}


def upgrade() -> None:
    bind = op.get_bind()
    for table in _TABLES:
        if not _has_index(bind, table):
            op.create_index(_index_name(table), table, [_COLUMN])


def downgrade() -> None:
    bind = op.get_bind()
    for table in reversed(_TABLES):
        if _has_index(bind, table):
            op.drop_index(_index_name(table), table_name=table)

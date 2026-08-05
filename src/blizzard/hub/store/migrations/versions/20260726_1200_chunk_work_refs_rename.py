"""``chunk_pm_pointers`` -> ``chunk_work_refs`` (issue #55, hub store tree). A **pure rename**, guarded
on the table names actually present. Earlier revisions keep the old name (``canon:no-retro``).

Revision ID: 20260726_1200_hub_chunk_work_refs_rename
Revises: 20260725_1200_hub_graph_checks_gating
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260726_1200_hub_chunk_work_refs_rename"
down_revision: str | None = "20260725_1200_hub_graph_checks_gating"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_OLD_NAME = "chunk_pm_pointers"
_NEW_NAME = "chunk_work_refs"


def _tables(bind: sa.Connection) -> set[str]:
    return set(sa.inspect(bind).get_table_names())


def upgrade() -> None:
    tables = _tables(op.get_bind())
    if _OLD_NAME in tables and _NEW_NAME not in tables:
        op.rename_table(_OLD_NAME, _NEW_NAME)


def downgrade() -> None:
    tables = _tables(op.get_bind())
    if _NEW_NAME in tables and _OLD_NAME not in tables:
        op.rename_table(_NEW_NAME, _OLD_NAME)

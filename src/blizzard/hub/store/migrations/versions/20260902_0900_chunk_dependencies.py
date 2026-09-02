"""chunk_dependencies — the declared dependent-on-prerequisite edge (issue #456). One
new table.

Revision ID: 20260902_0900_chunk_dependencies
Revises: 20260901_0900_finding_facts_finding_set_id
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

from blizzard.hub.store.schema import chunk_dependencies

revision: str = "20260902_0900_chunk_dependencies"
down_revision: str | None = "20260901_0900_finding_facts_finding_set_id"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLES = (chunk_dependencies,)


def upgrade() -> None:
    bind = op.get_bind()
    for table in _TABLES:
        table.create(bind, checkfirst=True)


def downgrade() -> None:
    bind = op.get_bind()
    for table in reversed(_TABLES):
        table.drop(bind, checkfirst=True)

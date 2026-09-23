"""routine lifecycle facts — a reversible retire/re-enable brake over one routine_id.
Append-only, newest-fact-wins, created ``checkfirst``; ``routines`` stays untouched.

Revision ID: 20260923_1000_routine_lifecycle_facts
Revises: 20260922_1200_hub_usage_estimated_cost
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

from blizzard.hub.store.schema import routine_lifecycle_facts

revision: str = "20260923_1000_routine_lifecycle_facts"
down_revision: str | None = "20260922_1200_hub_usage_estimated_cost"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLES = (routine_lifecycle_facts,)


def upgrade() -> None:
    bind = op.get_bind()
    for table in _TABLES:
        table.create(bind, checkfirst=True)


def downgrade() -> None:
    bind = op.get_bind()
    for table in reversed(_TABLES):
        table.drop(bind, checkfirst=True)

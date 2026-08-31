"""garden_proposal_closures — one closure fact per pass or accept (blizzard#395). One
new table.

Revision ID: 20260831_0945_garden_proposal_closures
Revises: 20260831_0930_finding_sets_routine_name
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

from blizzard.hub.store.schema import garden_proposal_closures

revision: str = "20260831_0945_garden_proposal_closures"
down_revision: str | None = "20260831_0930_finding_sets_routine_name"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLES = (garden_proposal_closures,)


def upgrade() -> None:
    bind = op.get_bind()
    for table in _TABLES:
        table.create(bind, checkfirst=True)


def downgrade() -> None:
    bind = op.get_bind()
    for table in reversed(_TABLES):
        table.drop(bind, checkfirst=True)

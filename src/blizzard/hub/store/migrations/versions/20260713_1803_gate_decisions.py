"""human-gate decisions, resolutions, and requeue facts, created in FK-dependency order
(hub store tree)

Revision ID: 20260713_1803_hub_gate_decisions
Revises: 20260713_1801_hub_questions_and_answers
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

from blizzard.hub.store.schema import decision_resolutions, decisions, requeues

revision: str = "20260713_1803_hub_gate_decisions"
down_revision: str | None = "20260713_1801_hub_questions_and_answers"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Parents before children so the FK constraints resolve.
_TABLES = [decisions, decision_resolutions, requeues]


def upgrade() -> None:
    bind = op.get_bind()
    for table in _TABLES:
        table.create(bind, checkfirst=True)


def downgrade() -> None:
    bind = op.get_bind()
    for table in reversed(_TABLES):
        table.drop(bind, checkfirst=True)

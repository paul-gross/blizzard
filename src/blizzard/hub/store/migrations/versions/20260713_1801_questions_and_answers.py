"""questions, answers, and answer-deliveries (hub store tree)

Revision ID: 20260713_1801_hub_questions_and_answers
Revises: 20260713_1716_hub_graph_node_produces_checks
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

from blizzard.hub.store.schema import answer_deliveries, question_answers, questions

revision: str = "20260713_1801_hub_questions_and_answers"
down_revision: str | None = "20260713_1716_hub_graph_node_produces_checks"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLES = (questions, question_answers, answer_deliveries)


def upgrade() -> None:
    bind = op.get_bind()
    for table in _TABLES:
        table.create(bind, checkfirst=True)


def downgrade() -> None:
    bind = op.get_bind()
    for table in reversed(_TABLES):
        table.drop(bind, checkfirst=True)

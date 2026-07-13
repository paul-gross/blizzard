"""questions, answers, and answer-deliveries (hub store tree)

P7 lands the ask/answer rendezvous (MVP criterion 7, [ask-answer.md]): a worker's
``blizzard runner ask`` becomes a durable ``questions`` row the chunk parks on, its
answer a ``question_answers`` row whose primary-key CAS makes answering
first-write-wins, and the resume-with-answer an ``answer_deliveries`` fact (board
detail). Before this the hub had no home for a question, so a parked worker's exit
looked like an ordinary verdict-less failure.

The hub store's Alembic tree targets one shared ``schema`` metadata whose table
objects reflect the *current* definition, so a fresh database's create runs the
head schema. This revision creates exactly the three new tables (the same
live-schema pattern the sibling revisions use), ``checkfirst`` so ``base -> head``
on a fresh store and an in-place upgrade of a pre-P7 store both converge.

Revision ID: 0006_hub_questions_and_answers
Revises: 0005_hub_graph_node_produces_checks
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

from blizzard.hub.store.schema import answer_deliveries, question_answers, questions

revision: str = "0006_hub_questions_and_answers"
down_revision: str | None = "0005_hub_graph_node_produces_checks"
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

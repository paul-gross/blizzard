"""questions, answers, and answer-deliveries (hub store tree)

Revision ID: 20260713_1801_hub_questions_and_answers
Revises: 20260713_1716_hub_graph_node_produces_checks
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260713_1801_hub_questions_and_answers"
down_revision: str | None = "20260713_1716_hub_graph_node_produces_checks"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Frozen shape: the live schema's later `harness_id` column would skip its own backfill.
_frozen_metadata = sa.MetaData()
sa.Table("chunks", _frozen_metadata, sa.Column("chunk_id", sa.String, primary_key=True))
questions = sa.Table(
    "questions",
    _frozen_metadata,
    sa.Column("question_id", sa.String, primary_key=True),
    sa.Column("chunk_id", sa.String, sa.ForeignKey("chunks.chunk_id"), nullable=False),
    sa.Column("node_id", sa.String, nullable=True),
    sa.Column("session_id", sa.String, nullable=True),
    sa.Column("runner_id", sa.String, nullable=False),
    sa.Column("epoch", sa.Integer, nullable=False),
    sa.Column("question", sa.Text, nullable=False),
    sa.Column("options", sa.Text, nullable=False),
    sa.Column("asked_at", sa.DateTime, nullable=False),
)
sa.Index("ix_questions_chunk_id", questions.c.chunk_id)
question_answers = sa.Table(
    "question_answers",
    _frozen_metadata,
    sa.Column("question_id", sa.String, sa.ForeignKey("questions.question_id"), primary_key=True),
    sa.Column("answer", sa.Text, nullable=False),
    sa.Column("answered_by", sa.String, nullable=False),
    sa.Column("answered_at", sa.DateTime, nullable=False),
)
answer_deliveries = sa.Table(
    "answer_deliveries",
    _frozen_metadata,
    sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
    sa.Column("question_id", sa.String, sa.ForeignKey("questions.question_id"), nullable=False),
    sa.Column("chunk_id", sa.String, sa.ForeignKey("chunks.chunk_id"), nullable=False),
    sa.Column("delivered_at", sa.DateTime, nullable=False),
)

_TABLES = (questions, question_answers, answer_deliveries)


def upgrade() -> None:
    bind = op.get_bind()
    for table in _TABLES:
        table.create(bind, checkfirst=True)


def downgrade() -> None:
    bind = op.get_bind()
    for table in reversed(_TABLES):
        table.drop(bind, checkfirst=True)

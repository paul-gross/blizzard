"""human-gate decisions, resolutions, and requeue facts, created in FK-dependency order
(hub store tree)

Revision ID: 20260713_1803_hub_gate_decisions
Revises: 20260713_1801_hub_questions_and_answers
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from blizzard.foundation.store.utc import UtcDateTime

revision: str = "20260713_1803_hub_gate_decisions"
down_revision: str | None = "20260713_1801_hub_questions_and_answers"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Frozen (``bzh:frozen-revisions``): ``decisions``, ``decision_resolutions`` and ``requeues`` are local literals
# of the hub store's declared shape. ``chunks`` is an FK-resolution stub: never created, never dropped.
_frozen_metadata = sa.MetaData()
sa.Table(
    "chunks",
    _frozen_metadata,
    sa.Column("chunk_id", sa.String, primary_key=True),
)
_decisions = sa.Table(
    "decisions",
    _frozen_metadata,
    sa.Column("decision_id", sa.String, primary_key=True),
    sa.Column("chunk_id", sa.String, sa.ForeignKey("chunks.chunk_id"), nullable=False),
    sa.Column("node_id", sa.String, nullable=False),
    sa.Column("node_name", sa.String, nullable=False),
    sa.Column("epoch", sa.Integer, nullable=False),
    sa.Column("choices", sa.Text, nullable=False),
    sa.Column("submitted_at", UtcDateTime, nullable=False),
    sa.Column("imposed_by_runner_id", sa.String, nullable=True),
)
sa.Index("ix_decisions_chunk_id", _decisions.c.chunk_id)
sa.Index("ix_decisions_submitted_at_decision_id", _decisions.c.submitted_at, _decisions.c.decision_id)
_decision_resolutions = sa.Table(
    "decision_resolutions",
    _frozen_metadata,
    sa.Column("decision_id", sa.String, sa.ForeignKey("decisions.decision_id"), primary_key=True),
    sa.Column("choice", sa.String, nullable=False),
    sa.Column("resolved_by", sa.String, nullable=False),
    sa.Column("resolved_at", UtcDateTime, nullable=False),
)
sa.Index(
    "ix_decision_resolutions_resolved_at_decision_id",
    _decision_resolutions.c.resolved_at,
    _decision_resolutions.c.decision_id,
)
_requeues = sa.Table(
    "requeues",
    _frozen_metadata,
    sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
    sa.Column("chunk_id", sa.String, sa.ForeignKey("chunks.chunk_id"), nullable=False),
    sa.Column("requeued_at", UtcDateTime, nullable=False),
)
sa.Index("ix_requeues_chunk_id", _requeues.c.chunk_id)
sa.Index("ix_requeues_requeued_at_id", _requeues.c.requeued_at, _requeues.c.id)

# Parents before children so the FK constraints resolve.
_TABLES = [_decisions, _decision_resolutions, _requeues]


def upgrade() -> None:
    bind = op.get_bind()
    for table in _TABLES:
        table.create(bind, checkfirst=True)


def downgrade() -> None:
    bind = op.get_bind()
    for table in reversed(_TABLES):
        table.drop(bind, checkfirst=True)

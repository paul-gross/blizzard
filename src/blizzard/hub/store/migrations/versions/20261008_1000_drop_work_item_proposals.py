"""Drop the retired worker work-item proposal storage: ``work_item_strikes``,
``work_item_materializations``, ``work_item_proposals``, and ``graph_nodes.proposes_work_items``.

Destructive: ``downgrade()`` recreates all four empty, never the rows.

Revision ID: 20261008_1000_drop_work_item_proposals
Revises: 20261006_1200_runner_minted_ids
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from blizzard.foundation.store.utc import UtcDateTime

revision: str = "20261008_1000_drop_work_item_proposals"
down_revision: str | None = "20261006_1200_runner_minted_ids"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_PROPOSALS = "work_item_proposals"
_MATERIALIZATIONS = "work_item_materializations"
_STRIKES = "work_item_strikes"
_PROPOSALS_INDEX = "ix_work_item_proposals_chunk_id"
_MATERIALIZATIONS_UNIQUE = "uq_work_item_materializations_proposal_id"
_COLUMN = "proposes_work_items"


def upgrade() -> None:
    op.drop_table(_STRIKES)
    op.drop_table(_MATERIALIZATIONS)
    op.drop_index(_PROPOSALS_INDEX, table_name=_PROPOSALS)
    op.drop_table(_PROPOSALS)
    with op.batch_alter_table("graph_nodes") as batch:
        batch.drop_column(_COLUMN)


def downgrade() -> None:
    with op.batch_alter_table("graph_nodes") as batch:
        batch.add_column(sa.Column(_COLUMN, sa.Boolean, nullable=True))
    op.create_table(
        _PROPOSALS,
        sa.Column("proposal_id", sa.String, primary_key=True),
        sa.Column("chunk_id", sa.String, sa.ForeignKey("chunks.chunk_id"), nullable=False),
        sa.Column("node_id", sa.String, nullable=False),
        sa.Column("node_name", sa.String, nullable=False),
        sa.Column("epoch", sa.Integer, nullable=False),
        sa.Column("ordinal", sa.Integer, nullable=False),
        sa.Column("kind", sa.String, nullable=False),
        sa.Column("data", sa.Text, nullable=False),
        sa.Column("proposed_at", UtcDateTime, nullable=False),
        sa.Column("runner_id", sa.String, nullable=True),
    )
    op.create_index(_PROPOSALS_INDEX, _PROPOSALS, ["chunk_id"])
    op.create_table(
        _MATERIALIZATIONS,
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("proposal_id", sa.String, sa.ForeignKey("work_item_proposals.proposal_id"), nullable=False),
        sa.Column("outcome", sa.String, nullable=False),
        sa.Column("source", sa.String, nullable=True),
        sa.Column("ref", sa.String, nullable=True),
        sa.Column("reason", sa.String, nullable=True),
        sa.Column("recorded_at", UtcDateTime, nullable=False),
        sa.UniqueConstraint("proposal_id", name=_MATERIALIZATIONS_UNIQUE),
    )
    op.create_table(
        _STRIKES,
        sa.Column("proposal_id", sa.String, sa.ForeignKey("work_item_proposals.proposal_id"), primary_key=True),
        sa.Column("decision_id", sa.String, sa.ForeignKey("decisions.decision_id"), nullable=False),
        sa.Column("struck_by", sa.String, nullable=False),
        sa.Column("struck_at", UtcDateTime, nullable=False),
    )

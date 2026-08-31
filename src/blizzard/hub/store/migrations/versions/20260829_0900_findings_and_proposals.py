"""findings, finding_facts, finding_sets, garden_proposals, garden_proposal_findings
(blizzard#390). One hand-written revision mints all five. `garden_proposals` is a frozen
local literal (`bzh:frozen-revisions`) — no `source_artifact_id`/`ref` — reshaped by
20260830_2015_garden_proposals_source_artifact.

Revision ID: 20260829_0900_findings_and_proposals
Revises: 20260828_1000_scopes_and_routines
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from blizzard.foundation.store.utc import UtcDateTime
from blizzard.hub.store.schema import finding_facts, finding_sets, findings, garden_proposal_findings

revision: str = "20260829_0900_findings_and_proposals"
down_revision: str | None = "20260828_1000_scopes_and_routines"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# This revision's own frozen shape — no `source_artifact_id`/`ref` — reshaped by
# 20260830_2015_garden_proposals_source_artifact.
_frozen_metadata = sa.MetaData()
_garden_proposals = sa.Table(
    "garden_proposals",
    _frozen_metadata,
    sa.Column("proposal_id", sa.String, primary_key=True),
    sa.Column("routine_name", sa.String, nullable=False),
    sa.Column("class", sa.String, key="class_", nullable=False),
    sa.Column("title", sa.String, nullable=False),
    sa.Column("body", sa.Text, nullable=False),
    sa.Column("created_at", UtcDateTime, nullable=False),
)
sa.Index("ix_garden_proposals_routine_class", _garden_proposals.c.routine_name, _garden_proposals.c.class_)

_TABLES = (findings, finding_sets, finding_facts, _garden_proposals, garden_proposal_findings)


def upgrade() -> None:
    bind = op.get_bind()
    for table in _TABLES:
        table.create(bind, checkfirst=True)


def downgrade() -> None:
    bind = op.get_bind()
    for table in reversed(_TABLES):
        table.drop(bind, checkfirst=True)

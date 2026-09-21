"""findings, finding_facts, finding_sets, garden_proposals, garden_proposal_findings
(blizzard#390). One hand-written revision mints all five; every table but the last is a
frozen local literal at its as-shipped shape (`bzh:frozen-revisions`), each reshaped by
a later revision. `garden_proposal_findings` is never reshaped, so it still imports live.

Revision ID: 20260829_0900_findings_and_proposals
Revises: 20260828_1000_scopes_and_routines
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from blizzard.foundation.store.utc import UtcDateTime
from blizzard.hub.store.schema import garden_proposal_findings

revision: str = "20260829_0900_findings_and_proposals"
down_revision: str | None = "20260828_1000_scopes_and_routines"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# `scopes`/`artifacts`/`chunks` are FK-resolution stubs: never created, never dropped.
_frozen_metadata = sa.MetaData()
sa.Table("scopes", _frozen_metadata, sa.Column("slug", sa.String, primary_key=True))
sa.Table("artifacts", _frozen_metadata, sa.Column("artifact_id", sa.String, primary_key=True))
sa.Table("chunks", _frozen_metadata, sa.Column("chunk_id", sa.String, primary_key=True))

_findings = sa.Table(
    "findings",
    _frozen_metadata,
    sa.Column("finding_id", sa.String, primary_key=True),
    sa.Column("routine_name", sa.String, nullable=False),
    sa.Column("scope_slug", sa.String, sa.ForeignKey("scopes.slug"), nullable=False),
    sa.Column("class", sa.String, key="class_", nullable=False),
    sa.Column("locus", sa.String, nullable=False),
    sa.Column("summary", sa.Text, nullable=False),
    sa.Column("introduced", sa.String, nullable=True),
)
sa.Index("ix_findings_routine_scope", _findings.c.routine_name, _findings.c.scope_slug)
sa.Index("ix_findings_routine_class", _findings.c.routine_name, _findings.c.class_)

# No `delivered` (blizzard#583) — reshaped by 20260920_1200_finding_delivered_state.
_finding_facts = sa.Table(
    "finding_facts",
    _frozen_metadata,
    sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
    sa.Column("finding_id", sa.String, sa.ForeignKey("findings.finding_id"), nullable=False),
    sa.Column("kind", sa.String, nullable=False),
    sa.Column("recorded_at", UtcDateTime, nullable=False),
    sa.Column("note", sa.Text, nullable=True),
    sa.CheckConstraint("kind IN ('add', 'observed', 'gone')", name="ck_finding_facts_kind"),
)
sa.Index("ix_finding_facts_finding_id_id", _finding_facts.c.finding_id, _finding_facts.c.id)

_finding_sets = sa.Table(
    "finding_sets",
    _frozen_metadata,
    sa.Column("finding_set_id", sa.String, primary_key=True),
    sa.Column("artifact_id", sa.String, sa.ForeignKey("artifacts.artifact_id"), nullable=False, unique=True),
    sa.Column("chunk_id", sa.String, sa.ForeignKey("chunks.chunk_id"), nullable=False),
    sa.Column("scope_slug", sa.String, sa.ForeignKey("scopes.slug"), nullable=False),
    sa.Column("revisions", sa.Text, nullable=False),
    sa.Column("measurement", sa.Text, nullable=True),
)
sa.Index("ix_finding_sets_chunk_id", _finding_sets.c.chunk_id)

# No `source_artifact_id`/`ref` — reshaped by 20260830_2015_garden_proposals_source_artifact.
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

_TABLES = (_findings, _finding_sets, _finding_facts, _garden_proposals, garden_proposal_findings)


def upgrade() -> None:
    bind = op.get_bind()
    for table in _TABLES:
        table.create(bind, checkfirst=True)


def downgrade() -> None:
    bind = op.get_bind()
    for table in reversed(_TABLES):
        table.drop(bind, checkfirst=True)

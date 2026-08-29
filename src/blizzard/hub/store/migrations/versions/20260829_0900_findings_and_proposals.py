"""findings, finding_facts, finding_sets, garden_proposals, garden_proposal_findings —
the finding-and-proposal hub entities (blizzard#390). One hand-written revision mints
all five.

Revision ID: 20260829_0900_findings_and_proposals
Revises: 20260828_1000_scopes_and_routines
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

from blizzard.hub.store.schema import (
    finding_facts,
    finding_sets,
    findings,
    garden_proposal_findings,
    garden_proposals,
)

revision: str = "20260829_0900_findings_and_proposals"
down_revision: str | None = "20260828_1000_scopes_and_routines"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLES = (findings, finding_sets, finding_facts, garden_proposals, garden_proposal_findings)


def upgrade() -> None:
    bind = op.get_bind()
    for table in _TABLES:
        table.create(bind, checkfirst=True)


def downgrade() -> None:
    bind = op.get_bind()
    for table in reversed(_TABLES):
        table.drop(bind, checkfirst=True)

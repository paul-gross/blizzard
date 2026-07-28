"""``chunk_migrations.source`` — what moved the chunk (hub store tree)

Issue #164 adds a third path onto :meth:`record_migration`: a standing follow-latest
policy, alongside #90's authored cross-graph edge and #124's operator-set intent. Without
a discriminator all three write byte-identical facts, so a chunk found on a graph it did
not start on has no way to say *why* — which matters most for the policy, the only one of
the three that moves a chunk with nobody having asked.

**Additive and nullable, with no backfill.** An existing row cannot be attributed: it came
from either the authored edge or an operator's intent, and the fact carries nothing that
tells them apart (same-name is not a tell — `hub chunk migrate` by name also resolves to a
newer same-name mint). Filling one in would fabricate provenance, so legacy rows stay NULL
and the domain reads that as *unrecorded* rather than as a value.

Guarded on the column already being present, like the other in-place column adds in this
tree, so a fresh ``base -> head`` (where ``schema``'s live metadata already declares it)
and an in-place upgrade of a live store both converge.

Revision ID: 20260728_1230_hub_chunk_migration_source
Revises: 20260728_1200_hub_graph_policy_facts
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260728_1230_hub_chunk_migration_source"
down_revision: str | None = "20260728_1200_hub_graph_policy_facts"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLE = "chunk_migrations"
_COLUMN = "source"


def _columns(bind: sa.Connection) -> set[str]:
    return {c["name"] for c in sa.inspect(bind).get_columns(_TABLE)}


def upgrade() -> None:
    bind = op.get_bind()
    if _COLUMN not in _columns(bind):
        op.add_column(_TABLE, sa.Column(_COLUMN, sa.String(), nullable=True))


def downgrade() -> None:
    bind = op.get_bind()
    if _COLUMN in _columns(bind):
        with op.batch_alter_table(_TABLE) as batch:
            batch.drop_column(_COLUMN)

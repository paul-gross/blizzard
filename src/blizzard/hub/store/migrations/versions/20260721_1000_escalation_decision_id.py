"""escalations.decision_id — close a gate decision an unresolvable migration escalated

Issue #110 completes the issue-#90 fix: a **human gate's** resolved choice that migrates
cross-graph to a target that resolves to ``None`` (unminted or retired) records an
escalation and writes neither a ``transitions`` nor a ``chunk_migrations`` row — so the
gate's decision stayed ``transitioned=False`` forever (a phantom live decision that
wedges REAP recovery and drives a per-tick runner re-submit). This revision adds the
column the escalation stamps that decision id into, so the decision derives closed here
too, exactly as ``chunk_migrations.decision_id`` closes it on the resolvable branch.

Nullable: the ordinary retries-exhausted escalation resolves no decision and reads back
as ``None``, and any escalation row written before this column existed predates it —
the same tolerance ``20260719_2000_hub_chunk_stopped_by`` gives ``stopped_by``.

Idempotent like that revision: the column is added only where an older database lacks
it, so a fresh ``base -> head`` and an in-place upgrade both land at exactly one column.

Revision ID: 20260721_1000_hub_escalation_decision_id
Revises: 20260720_1000_hub_chunk_intended_migration
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260721_1000_hub_escalation_decision_id"
down_revision: str | None = "20260720_1000_hub_chunk_intended_migration"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLE = "escalations"
_COLUMN = "decision_id"


def _has_column(bind: sa.Connection) -> bool:
    return _COLUMN in {c["name"] for c in sa.inspect(bind).get_columns(_TABLE)}


def upgrade() -> None:
    bind = op.get_bind()
    if not _has_column(bind):
        op.add_column(_TABLE, sa.Column(_COLUMN, sa.String(), nullable=True))


def downgrade() -> None:
    bind = op.get_bind()
    if _has_column(bind):
        op.drop_column(_TABLE, _COLUMN)

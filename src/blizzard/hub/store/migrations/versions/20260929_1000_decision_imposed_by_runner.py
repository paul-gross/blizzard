"""Decision origin — the runner whose configuration imposed a gate, backfilled for worker-judged nodes.

Revision ID: 20260929_1000_decision_imposed_by_runner
Revises: 20260928_1100_runner_retirement
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260929_1000_decision_imposed_by_runner"
down_revision: str | None = "20260928_1100_runner_retirement"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_DECISIONS = "decisions"
_REGISTRATIONS = "runner_registrations"
_COLUMN = "imposed_by_runner_id"
_GATES = "gates"

# Only a runner-config gate opens a decision at a worker-judged node; no lease match stays NULL.
_BACKFILL = sa.text(
    """
    UPDATE decisions
    SET imposed_by_runner_id = (
        SELECT lease_facts.runner_id FROM lease_facts
        WHERE lease_facts.chunk_id = decisions.chunk_id AND lease_facts.epoch = decisions.epoch
        ORDER BY lease_facts.id DESC LIMIT 1
    )
    WHERE decisions.node_id IN (SELECT node_id FROM graph_nodes WHERE judged_by = 'worker')
    """
)


def _has_column(bind: sa.Connection, table: str, column: str) -> bool:
    return column in {c["name"] for c in sa.inspect(bind).get_columns(table)}


def upgrade() -> None:
    bind = op.get_bind()
    if not _has_column(bind, _REGISTRATIONS, _GATES):
        op.add_column(_REGISTRATIONS, sa.Column(_GATES, sa.Text(), nullable=True))
    if not _has_column(bind, _DECISIONS, _COLUMN):
        op.add_column(_DECISIONS, sa.Column(_COLUMN, sa.String(), nullable=True))
    bind.execute(_BACKFILL)


def downgrade() -> None:
    bind = op.get_bind()
    if _has_column(bind, _DECISIONS, _COLUMN):
        with op.batch_alter_table(_DECISIONS) as batch:
            batch.drop_column(_COLUMN)
    if _has_column(bind, _REGISTRATIONS, _GATES):
        with op.batch_alter_table(_REGISTRATIONS) as batch:
            batch.drop_column(_GATES)

"""event_log.runner_id becomes nullable — a hub-authored event names no runner
(blizzard-context:/domain/operations.md), and downgrading restores the legacy sentinel.

Revision ID: 20260907_1000_event_log_runner_id_nullable
Revises: 20260907_0900_drop_node_mode
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260907_1000_event_log_runner_id_nullable"
down_revision: str | None = "20260907_0900_drop_node_mode"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLE = "event_log"

# The pre-downgrade sentinel — restated, not imported, from
# ``blizzard.hub.delivery.hub_node._HUB_RUNNER_ID`` (``bzh:frozen-revisions``).
_HUB_RUNNER_ID = "hub"

_EVENT_LOG = sa.Table(
    "event_log",
    sa.MetaData(),
    sa.Column("id", sa.Integer, primary_key=True),
    sa.Column("runner_id", sa.String, nullable=True),
)


def _is_nullable(bind: sa.Connection) -> bool:
    columns = {c["name"]: c for c in sa.inspect(bind).get_columns(_TABLE)}
    return bool(columns["runner_id"]["nullable"])


def upgrade() -> None:
    bind = op.get_bind()
    if _is_nullable(bind):
        return  # already reshaped — this revision's own guard, not per-row

    # `recreate="always"` is dialect-agnostic (`bzh:sql-portable`): one copy-based
    # rebuild widens the column on both sqlite and postgres, no dialect branch.
    with op.batch_alter_table(_TABLE, recreate="always") as batch:
        batch.alter_column("runner_id", nullable=True)


def downgrade() -> None:
    bind = op.get_bind()
    if not _is_nullable(bind):
        return  # already the pre-reshape shape

    # Every stored null-runner row is hub-authored (projected escalations are
    # synthesized, never written), so this restore is exactly reversible.
    bind.execute(_EVENT_LOG.update().where(_EVENT_LOG.c.runner_id.is_(None)).values(runner_id=_HUB_RUNNER_ID))

    with op.batch_alter_table(_TABLE, recreate="always") as batch:
        batch.alter_column("runner_id", nullable=False)

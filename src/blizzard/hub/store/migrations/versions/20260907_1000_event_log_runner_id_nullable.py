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
# ``blizzard.hub.domain.work_closure._HUB_RUNNER_ID`` (``bzh:frozen-revisions``), the
# sentinel this revision's ``event_log`` backfill actually restates; the module has
# since dropped it.
_HUB_RUNNER_ID = "hub"

# The three hub-authored kinds — restated, not imported, from
# ``blizzard.hub.domain.work_closure._EVENT_CLOSED``/``_EVENT_CLOSE_FAILED`` and
# ``blizzard.hub.delivery.hub_node._EVENT_UNROUTABLE_OUTCOME`` (``bzh:frozen-revisions``).
# Matching on kind, not on the ``'hub'`` value alone, spares an operator-named runner
# literally called "hub" from being nulled by this backfill.
_HUB_AUTHORED_KINDS = ("work-item-closed", "work-item-close-failed", "hub-node-unroutable-outcome")

_EVENT_LOG = sa.Table(
    "event_log",
    sa.MetaData(),
    sa.Column("id", sa.Integer, primary_key=True),
    sa.Column("kind", sa.String, nullable=False),
    sa.Column("runner_id", sa.String, nullable=True),
)


def _is_nullable(bind: sa.Connection) -> bool:
    columns = {c["name"]: c for c in sa.inspect(bind).get_columns(_TABLE)}
    return bool(columns["runner_id"]["nullable"])


def upgrade() -> None:
    bind = op.get_bind()
    if not _is_nullable(bind):
        # Default `recreate="auto"` (`bzh:sql-portable`): sqlite has no in-place ALTER
        # and batch-copies the table; postgres alters the column directly, which
        # `recreate="always"` would forgo, dropping `id`'s existing SERIAL sequence
        # in the copy since the postgres DDL compiler's SERIAL branch never inspects
        # the reflected `server_default` that carries it.
        with op.batch_alter_table(_TABLE) as batch:
            batch.alter_column("runner_id", nullable=True)

    # Backfill: every existing hub-authored row stops naming the synthetic sentinel,
    # so live history stops producing the phantom runner filter chip too. Idempotent —
    # a rerun with no remaining sentinel rows updates nothing. Matched by kind, not by
    # the ``'hub'`` value alone, so an operator-named runner called "hub" is untouched.
    bind.execute(_EVENT_LOG.update().where(_EVENT_LOG.c.kind.in_(_HUB_AUTHORED_KINDS)).values(runner_id=None))


def downgrade() -> None:
    bind = op.get_bind()
    if not _is_nullable(bind):
        return  # already the pre-reshape shape

    # Every stored null-runner row is hub-authored (projected escalations are
    # synthesized, never written), so this restore is exactly reversible.
    bind.execute(_EVENT_LOG.update().where(_EVENT_LOG.c.runner_id.is_(None)).values(runner_id=_HUB_RUNNER_ID))

    with op.batch_alter_table(_TABLE) as batch:
        batch.alter_column("runner_id", nullable=False)

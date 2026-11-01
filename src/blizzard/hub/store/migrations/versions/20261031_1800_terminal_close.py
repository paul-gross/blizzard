"""Enqueue outstanding refs on pre-existing reserved-terminal transitions.

The earlier close-intent backfill covered landing and hand completion, but not
transitions to done. Preserve existing decisions and let the normal drain decide
whether each newly enqueued ref can be closed.

Revision ID: 20261031_1800_terminal_close
Revises: 20261029_1200_landing_policies
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20261031_1800_terminal_close"
down_revision: str | None = "20261029_1200_landing_policies"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Frozen SQL: every correlation includes the tenant, including exclusions.
    op.execute(
        sa.text("""
        INSERT INTO close_intents (tenant_id, chunk_id, source, ref, enqueued_at, retired_at)
        SELECT r.tenant_id, r.chunk_id, r.source, r.ref, MIN(t.recorded_at), NULL
        FROM chunk_work_refs r
        JOIN transitions t ON t.tenant_id = r.tenant_id AND t.chunk_id = r.chunk_id
            AND t.to_node_id = 'done'
        WHERE NOT EXISTS (SELECT 1 FROM chunk_grouped g
                          WHERE g.tenant_id = r.tenant_id AND g.chunk_id = r.chunk_id)
          AND NOT EXISTS (SELECT 1 FROM chunk_deleted d
                          WHERE d.tenant_id = r.tenant_id AND d.chunk_id = r.chunk_id)
          AND NOT EXISTS (SELECT 1 FROM transitions newer
                          WHERE newer.tenant_id = t.tenant_id AND newer.chunk_id = t.chunk_id
                            AND (newer.recorded_at > t.recorded_at OR
                                 (newer.recorded_at = t.recorded_at AND newer.epoch >= t.epoch))
                            AND (newer.to_node_id != 'done' OR newer.recorded_at > t.recorded_at))
          AND NOT EXISTS (SELECT 1 FROM chunk_migrations newer
                          WHERE newer.tenant_id = t.tenant_id AND newer.chunk_id = t.chunk_id
                            AND (newer.recorded_at > t.recorded_at OR
                                 (newer.recorded_at = t.recorded_at AND newer.epoch >= t.epoch)))
          AND NOT EXISTS (SELECT 1 FROM chunk_restarts newer
                          WHERE newer.tenant_id = t.tenant_id AND newer.chunk_id = t.chunk_id
                            AND (newer.recorded_at > t.recorded_at OR
                                 (newer.recorded_at = t.recorded_at AND newer.epoch >= t.epoch)))
          AND NOT EXISTS (SELECT 1 FROM close_intents i
                          WHERE i.tenant_id = r.tenant_id AND i.chunk_id = r.chunk_id
                            AND i.source = r.source AND i.ref = r.ref)
          AND NOT EXISTS (SELECT 1 FROM work_item_closures c
                          WHERE c.tenant_id = r.tenant_id AND c.chunk_id = r.chunk_id
                            AND c.source = r.source AND c.ref = r.ref
                            AND c.outcome IN ('closed', 'gone', 'skipped'))
        GROUP BY r.tenant_id, r.chunk_id, r.source, r.ref
    """)
    )


def downgrade() -> None:
    # These are durable outbox facts, indistinguishable from later enqueues.
    pass

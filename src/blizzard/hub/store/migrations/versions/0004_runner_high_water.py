"""store-and-forward high-water mark (hub store tree)

P7 store-and-forward (D-069) gives the runner→hub fact push (POST /events) its
per-runner idempotency memory: ``runner_high_water`` records the greatest per-runner
sequence number the hub has already applied, so a replayed fact (lost ack, outage
backlog) is re-acked without re-applying. Defined once in
``blizzard.hub.store.schema``; this revision creates exactly that one table and
touches none of the earlier revisions'.

Revision ID: 0004_hub_runner_high_water
Revises: 0003_hub_escalation_takeover
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

from blizzard.hub.store.schema import runner_high_water

revision: str = "0004_hub_runner_high_water"
down_revision: str | None = "0003_hub_escalation_takeover"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    runner_high_water.create(op.get_bind(), checkfirst=False)


def downgrade() -> None:
    runner_high_water.drop(op.get_bind(), checkfirst=False)

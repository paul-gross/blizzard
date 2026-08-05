"""usage facts — harness cost/token telemetry per invocation, hub-side (issue #59, hub store tree)

One append-only row per invocation, **not** epoch-fenced: stale spend is real spend.
Revision ID: 20260717_2330_hub_usage_facts
Revises: 20260717_2318_hub_chunk_model_selection
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

from blizzard.hub.store.schema import usage_facts

revision: str = "20260717_2330_hub_usage_facts"
down_revision: str | None = "20260717_2318_hub_chunk_model_selection"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLES = (usage_facts,)


def upgrade() -> None:
    bind = op.get_bind()
    for table in _TABLES:
        table.create(bind, checkfirst=True)


def downgrade() -> None:
    bind = op.get_bind()
    for table in reversed(_TABLES):
        table.drop(bind, checkfirst=True)

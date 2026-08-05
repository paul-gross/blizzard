"""fleet registry — runner registrations and pause/resume facts (hub store tree)

``runner_pause_facts`` is append-only, newest-fact-wins; parents before children.
Revision ID: 20260713_1947_hub_runner_registry
Revises: 20260713_1946_hub_queue_shaping
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

from blizzard.hub.store.schema import runner_pause_facts, runner_registrations

revision: str = "20260713_1947_hub_runner_registry"
down_revision: str | None = "20260713_1946_hub_queue_shaping"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLES = [runner_registrations, runner_pause_facts]


def upgrade() -> None:
    bind = op.get_bind()
    for table in _TABLES:
        table.create(bind, checkfirst=True)


def downgrade() -> None:
    bind = op.get_bind()
    for table in reversed(_TABLES):
        table.drop(bind, checkfirst=True)

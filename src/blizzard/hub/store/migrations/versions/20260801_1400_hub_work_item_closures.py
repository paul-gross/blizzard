"""work_item_closures — delivery closure attempt outcomes (issue #216, hub store tree)

One row per close ATTEMPT outcome against a delivered chunk's work item —
``DeliveryClosureReconciler``'s durable record. Mirrors ``20260721_1600_hub_event_log``'s
live-schema pattern: this revision creates exactly the one new table, ``checkfirst`` so a
fresh ``base -> head`` and an in-place upgrade both converge. ``table.create(bind,
checkfirst=True)`` also emits ``uq_work_item_closures_chunk_source_ref_outcome`` declared
alongside the table in ``schema.py`` — it rides the same ``CREATE``.

Revision ID: 20260801_1400_hub_work_item_closures
Revises: 20260731_1200_hub_transitions_recorded_at
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

from blizzard.hub.store.schema import work_item_closures

revision: str = "20260801_1400_hub_work_item_closures"
down_revision: str | None = "20260731_1200_hub_transitions_recorded_at"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLES = (work_item_closures,)


def upgrade() -> None:
    bind = op.get_bind()
    for table in _TABLES:
        table.create(bind, checkfirst=True)


def downgrade() -> None:
    bind = op.get_bind()
    for table in reversed(_TABLES):
        table.drop(bind, checkfirst=True)

"""event_log — the hub's durable, append-only operational event feed (issue #125, hub store tree)

Phase 1 of the operational event log: one append-only row per operational event the
hub records (typed, severity-ranked, clock-stamped), unified at read time with open
escalations (``GET /api/events``, ``bzh:facts-not-status`` — no ``status`` column).
``chunk_id`` is nullable — some events are runner-scoped, naming no chunk.

``table.create(bind, checkfirst=True)`` also emits the ``ix_event_log_recorded_at``
index declared alongside the ``event_log`` table in ``schema.py`` — it is associated
with that table's metadata, so it rides the same ``CREATE`` (mirrors
``20260721_1100_hub_auth_identity_spine``'s ``uq_users_email``). The live-schema
pattern (mirrors ``20260717_2330_hub_usage_facts``): this revision creates exactly the
one new table, ``checkfirst`` so a fresh ``base -> head`` and an in-place upgrade both
converge.

Revision ID: 20260721_1600_hub_event_log
Revises: 20260721_1500_hub_cli_auth_state_user
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

from blizzard.hub.store.schema import event_log

revision: str = "20260721_1600_hub_event_log"
down_revision: str | None = "20260721_1500_hub_cli_auth_state_user"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLES = (event_log,)


def upgrade() -> None:
    bind = op.get_bind()
    for table in _TABLES:
        table.create(bind, checkfirst=True)


def downgrade() -> None:
    bind = op.get_bind()
    for table in reversed(_TABLES):
        table.drop(bind, checkfirst=True)

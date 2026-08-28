"""scopes, scope_lifecycle_facts, routines — the routine-and-scope hub entities
(blizzard#389). One hand-written revision mints all three.

Revision ID: 20260828_1000_scopes_and_routines
Revises: 20260826_0930_close_intents_backfill
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

from blizzard.hub.store.schema import routines, scope_lifecycle_facts, scopes

revision: str = "20260828_1000_scopes_and_routines"
down_revision: str | None = "20260826_0930_close_intents_backfill"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLES = (scopes, scope_lifecycle_facts, routines)


def upgrade() -> None:
    bind = op.get_bind()
    for table in _TABLES:
        table.create(bind, checkfirst=True)


def downgrade() -> None:
    bind = op.get_bind()
    for table in reversed(_TABLES):
        table.drop(bind, checkfirst=True)

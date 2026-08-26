"""close_intents — the durable close-intent outbox (blizzard#383). One new table.

Revision ID: 20260826_0900_close_intents
Revises: 20260825_1300_work_item_strikes
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

from blizzard.hub.store.schema import close_intents

revision: str = "20260826_0900_close_intents"
down_revision: str | None = "20260825_1300_work_item_strikes"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLES = (close_intents,)


def upgrade() -> None:
    bind = op.get_bind()
    for table in _TABLES:
        table.create(bind, checkfirst=True)


def downgrade() -> None:
    bind = op.get_bind()
    for table in reversed(_TABLES):
        table.drop(bind, checkfirst=True)

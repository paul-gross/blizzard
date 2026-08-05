"""event_log — creates the hub's durable, append-only operational event feed: typed,
severity-ranked, clock-stamped (issue #125, hub store tree)

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

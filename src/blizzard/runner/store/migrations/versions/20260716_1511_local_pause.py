"""local pause facts — the runner's own brake, distinct from the hub's (runner store tree)

Locally-minted append-only facts, newest-wins; effective paused is the OR of the two brakes.
Revision ID: 20260716_1511_runner_local_pause
Revises: 20260716_0532_runner_crash_recovery_context
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

from blizzard.runner.store.schema import local_pause_facts

revision: str = "20260716_1511_runner_local_pause"
down_revision: str | None = "20260716_0532_runner_crash_recovery_context"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLES = (local_pause_facts,)


def upgrade() -> None:
    bind = op.get_bind()
    for table in _TABLES:
        table.create(bind, checkfirst=True)


def downgrade() -> None:
    bind = op.get_bind()
    for table in reversed(_TABLES):
        table.drop(bind, checkfirst=True)

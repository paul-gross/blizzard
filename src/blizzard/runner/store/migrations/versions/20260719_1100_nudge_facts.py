"""runner-side nudge-once fact — ``nudge_facts``, at most one row per ``(lease_id,
epoch)``, written before the resume it guards (runner store tree, issue #113)

Revision ID: 20260719_1100_runner_nudge_facts
Revises: 20260719_1000_runner_attachments
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

from blizzard.runner.store.schema import nudge_facts

revision: str = "20260719_1100_runner_nudge_facts"
down_revision: str | None = "20260719_1000_runner_attachments"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLES = (nudge_facts,)


def upgrade() -> None:
    bind = op.get_bind()
    for table in _TABLES:
        table.create(bind, checkfirst=True)


def downgrade() -> None:
    bind = op.get_bind()
    for table in reversed(_TABLES):
        table.drop(bind, checkfirst=True)

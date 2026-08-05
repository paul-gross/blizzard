"""worker attachment channel — attachments (runner store tree, issue #113 Phase 2)

Append-only, one row per attach call, latest-wins per ``(lease_id, name)``; ``checkfirst``.
Revision ID: 20260719_1000_runner_attachments
Revises: 20260719_0900_runner_lease_tokens
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

from blizzard.runner.store.schema import attachments

revision: str = "20260719_1000_runner_attachments"
down_revision: str | None = "20260719_0900_runner_lease_tokens"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLES = (attachments,)


def upgrade() -> None:
    bind = op.get_bind()
    for table in _TABLES:
        table.create(bind, checkfirst=True)


def downgrade() -> None:
    bind = op.get_bind()
    for table in reversed(_TABLES):
        table.drop(bind, checkfirst=True)

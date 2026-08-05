"""route capability token stash — route_tokens (runner store tree, issue #84a). One
upserted row per chunk; the runner keeps no rotation history, only its current token.

Revision ID: 20260718_1200_runner_route_tokens
Revises: 20260717_2200_runner_usage_facts
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

from blizzard.runner.store.schema import route_tokens

revision: str = "20260718_1200_runner_route_tokens"
down_revision: str | None = "20260717_2200_runner_usage_facts"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLES = (route_tokens,)


def upgrade() -> None:
    bind = op.get_bind()
    for table in _TABLES:
        table.create(bind, checkfirst=True)


def downgrade() -> None:
    bind = op.get_bind()
    for table in reversed(_TABLES):
        table.drop(bind, checkfirst=True)

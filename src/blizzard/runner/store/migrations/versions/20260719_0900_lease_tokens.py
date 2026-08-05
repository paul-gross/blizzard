"""lease capability token stash — ``lease_tokens`` (runner store tree, issue #113): one row per lease,
written at spawn. The plaintext rides the spawn env and is never persisted, only its sha256.

Revision ID: 20260719_0900_runner_lease_tokens
Revises: 20260718_1200_runner_route_tokens
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

from blizzard.runner.store.schema import lease_tokens

revision: str = "20260719_0900_runner_lease_tokens"
down_revision: str | None = "20260718_1200_runner_route_tokens"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLES = (lease_tokens,)


def upgrade() -> None:
    bind = op.get_bind()
    for table in _TABLES:
        table.create(bind, checkfirst=True)


def downgrade() -> None:
    bind = op.get_bind()
    for table in reversed(_TABLES):
        table.drop(bind, checkfirst=True)

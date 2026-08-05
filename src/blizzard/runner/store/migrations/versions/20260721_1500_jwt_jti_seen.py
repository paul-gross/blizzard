"""SSO federation jti replay cache — the store-backed single-use guard a hub-signed JWT's
`jti` is checked against (runner store tree, issue #95, D4)

Revision ID: 20260721_1500_runner_jwt_jti_seen
Revises: 20260719_1100_runner_nudge_facts
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

from blizzard.runner.store.schema import jwt_jti_seen

revision: str = "20260721_1500_runner_jwt_jti_seen"
down_revision: str | None = "20260719_1100_runner_nudge_facts"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLES = (jwt_jti_seen,)


def upgrade() -> None:
    bind = op.get_bind()
    for table in _TABLES:
        table.create(bind, checkfirst=True)


def downgrade() -> None:
    bind = op.get_bind()
    for table in reversed(_TABLES):
        table.drop(bind, checkfirst=True)

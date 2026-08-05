"""external usage samples — one append-only row per sampling attempt, ``payload`` NULL
when the attempt produced nothing (runner store tree, issue #218)

Revision ID: 20260801_1500_runner_external_usage_samples
Revises: 20260728_1500_runner_lease_session_stamps
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

from blizzard.runner.store.schema import external_usage_samples

revision: str = "20260801_1500_runner_external_usage_samples"
down_revision: str | None = "20260728_1500_runner_lease_session_stamps"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLES = (external_usage_samples,)


def upgrade() -> None:
    bind = op.get_bind()
    for table in _TABLES:
        table.create(bind, checkfirst=True)


def downgrade() -> None:
    bind = op.get_bind()
    for table in reversed(_TABLES):
        table.drop(bind, checkfirst=True)

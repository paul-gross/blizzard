"""external usage samples — the harness's own subscription rate-limit windows (runner store tree, issue #218)

Phase 2 lands the runner-local half of surfacing a metered harness's rate-limit window
utilization: one append-only row per tick's sampling attempt, ``payload`` NULL when the
attempt produced nothing. The cadence anchor the tick gate reads is *derived* as
``max(sampled_at)`` over this table — never a separately-stored "last sampled" column — so
this migration creates exactly the one new table and nothing else.

Each revision in this tree creates a subset of the current ``schema`` metadata's tables
(the live-schema pattern); this one creates exactly the one new table, ``checkfirst`` so
a fresh ``base -> head`` and an in-place upgrade both converge.

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

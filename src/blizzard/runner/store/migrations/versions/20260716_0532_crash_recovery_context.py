"""spawn-generation + daemon-liveness fact tables (issue #13): ``lease_spawns`` scopes a recovery check
to the process running *now*; ``daemon_liveness`` separates downtime from idle-at-crash.

Revision ID: 20260716_0532_runner_crash_recovery_context
Revises: 20260715_1641_runner_session_ends
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

from blizzard.runner.store.schema import daemon_liveness, lease_spawns

revision: str = "20260716_0532_runner_crash_recovery_context"
down_revision: str | None = "20260715_1641_runner_session_ends"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    bind = op.get_bind()
    lease_spawns.create(bind, checkfirst=True)
    daemon_liveness.create(bind, checkfirst=True)


def downgrade() -> None:
    bind = op.get_bind()
    daemon_liveness.drop(bind, checkfirst=True)
    lease_spawns.drop(bind, checkfirst=True)

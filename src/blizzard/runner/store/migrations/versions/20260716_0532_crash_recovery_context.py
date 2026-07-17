"""spawn-generation + daemon-liveness fact tables (runner store tree)

The two facts startup crash-recovery needs to *interpret* what it finds (issue #13,
D-055/D-082). 0009 gave the scan its signals; this gives them the context that makes
them readable at recovery time:

* ``lease_spawns`` — when the lease's current process was spawned. A lease outlives its
  sessions (the ask/answer and resume paths re-spawn under the same lease and session),
  so a bare "this lease recorded a session-end" is true forever after the first natural
  exit and wrongly suppressed the resume of every later crash on that lease. Scoping the
  check to the newest spawn makes it a statement about the process running *now*.
* ``daemon_liveness`` — when the runner was last known alive, stamped by the tick. The
  staleness question is "was the worker still working when the daemon died", but a
  restart only has the clock at recovery: ``now - last_heartbeat`` measures
  ``downtime + idle-at-crash``, so any outage past the threshold read every in-flight
  lease as stalled and skipped it — defeating the reboot case the issue exists for.

Each revision in this tree creates a subset of the current ``schema`` metadata's tables
(the live-schema pattern); this one creates exactly the two new tables, ``checkfirst`` so
a fresh ``base -> head`` and an in-place upgrade both converge.

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

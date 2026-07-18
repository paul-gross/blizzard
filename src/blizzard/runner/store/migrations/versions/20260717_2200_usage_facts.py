"""usage facts — harness cost/token telemetry per invocation (runner store tree, issue #58)

Epic #57's cost-observability half: one append-only row per harness invocation (spawn /
resume / judge) whose usage the runner extracted, either off the harness's own result
envelope or, envelope-less, summed off the raw session transcript with ``cost_usd`` left
absent. Usage is a fact, never a stored aggregate (``bzh:facts-not-status``) — a chunk's
cost is derived by summing these at read time (the hub's job, Phase 3 of this epic); the
runner-local half here just lands the fact and buffers its outbound report on the same
store-and-forward rails as ``lease.minted``.

Each revision in this tree creates a subset of the current ``schema`` metadata's tables
(the live-schema pattern); this one creates exactly the one new table, ``checkfirst`` so
a fresh ``base -> head`` and an in-place upgrade both converge.

Revision ID: 20260717_2200_runner_usage_facts
Revises: 20260717_0446_runner_pause_parks
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

from blizzard.runner.store.schema import usage_facts

revision: str = "20260717_2200_runner_usage_facts"
down_revision: str | None = "20260717_2300_runner_requeues"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLES = (usage_facts,)


def upgrade() -> None:
    bind = op.get_bind()
    for table in _TABLES:
        table.create(bind, checkfirst=True)


def downgrade() -> None:
    bind = op.get_bind()
    for table in reversed(_TABLES):
        table.drop(bind, checkfirst=True)

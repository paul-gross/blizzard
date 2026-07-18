"""requeues — the operator's explicit hand-back after a human hold (issue #53)

One brand-new table, no reshape of any existing table (mirroring
``20260717_2245_takeovers.py``'s freeze-hazard note): the runner tree declares no
ForeignKeys at all, so neither half of that hazard applies here either.

Revision ID: 20260717_2300_runner_requeues
Revises: 20260717_2245_runner_takeovers
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

from blizzard.runner.store.schema import requeues

revision: str = "20260717_2300_runner_requeues"
down_revision: str | None = "20260717_2245_runner_takeovers"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    bind = op.get_bind()
    requeues.create(bind, checkfirst=True)


def downgrade() -> None:
    bind = op.get_bind()
    requeues.drop(bind, checkfirst=True)

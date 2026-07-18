"""takeovers — the operator's interactive session over a parked chunk (issue #52)

Two brand-new tables, no reshape of any existing table (mirroring
``20260717_0446_pause_parks.py``'s freeze-hazard note): the runner tree declares no
ForeignKeys at all, so neither half of that hazard applies here either.

Revision ID: 20260717_2245_runner_takeovers
Revises: 20260717_0446_runner_pause_parks
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

from blizzard.runner.store.schema import takeover_ends, takeovers

revision: str = "20260717_2245_runner_takeovers"
down_revision: str | None = "20260717_0446_runner_pause_parks"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLES = (takeovers, takeover_ends)


def upgrade() -> None:
    bind = op.get_bind()
    for table in _TABLES:
        table.create(bind, checkfirst=True)


def downgrade() -> None:
    bind = op.get_bind()
    for table in reversed(_TABLES):
        table.drop(bind, checkfirst=True)

"""takeover entries — the new ``takeover_entries`` table, one row per takeover whose entry completed (the commands
that displace a live worker ran, and the person was handed the session). An open takeover with no entry was interrupted
mid-entry, which startup recovery finishes and ends.

The upgrade creates the table and inserts an entry for every takeover open at that moment, stamped with its own opening
instant: a person's takeover opened under the prior release is entered, so the first startup on this one leaves it
alone. Takeovers already ended get no entry. A rerun skips the table when present and any takeover already entered.
``downgrade()`` drops the table; a re-upgrade backfills again.

Revision ID: 20261029_1000_takeover_entries
Revises: 20261026_1000_fact_append_sequence
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20261029_1000_takeover_entries"
down_revision: str | None = "20261026_1000_fact_append_sequence"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLE = "takeover_entries"

# The frozen shapes this revision reads, never the live schema, which keeps changing after it.
_takeovers = sa.table(
    "takeovers", sa.column("takeover_id", sa.String), sa.column("opened_at", sa.DateTime), sa.column("runner_id", sa.String)
)
_ends = sa.table("takeover_ends", sa.column("takeover_id", sa.String))
_entries = sa.table(
    _TABLE,
    sa.column("takeover_id", sa.String),
    sa.column("entered_at", sa.DateTime),
    sa.column("runner_id", sa.String),
)


def upgrade() -> None:
    bind = op.get_bind()
    if _TABLE not in sa.inspect(bind).get_table_names():
        op.create_table(
            _TABLE,
            sa.Column("takeover_id", sa.String(), sa.ForeignKey("takeovers.takeover_id"), primary_key=True),
            sa.Column("entered_at", sa.DateTime(), nullable=False),
            sa.Column("runner_id", sa.String(), nullable=False),
        )
    open_unentered = (
        sa.select(_takeovers.c.takeover_id, _takeovers.c.opened_at, _takeovers.c.runner_id)
        .where(_takeovers.c.takeover_id.not_in(sa.select(_ends.c.takeover_id)))
        .where(_takeovers.c.takeover_id.not_in(sa.select(_entries.c.takeover_id)))
    )
    bind.execute(sa.insert(_entries).from_select(["takeover_id", "entered_at", "runner_id"], open_unentered))


def downgrade() -> None:
    if _TABLE in sa.inspect(op.get_bind()).get_table_names():
        op.drop_table(_TABLE)

"""Give ``in_flight_elicitations`` the same ``sqlite_autoincrement`` fix as
``outbound_buffer``/``transcript_outbound_buffer`` (blizzard-context:/standards/persistence.md): a
deleted row's ``id`` must never be reissued, since ``pause_parks.interrupted_elicitation_id``
now names an in-flight elicitation by that id.

Revision ID: 20260924_0500_in_flight_elicitations_autoincrement
Revises: 20260924_0400_pause_park_interrupted_elicitation
"""

from __future__ import annotations

from alembic import op

revision = "20260924_0500_in_flight_elicitations_autoincrement"
down_revision = "20260924_0400_pause_park_interrupted_elicitation"
branch_labels = None
depends_on = None

_TABLE = "in_flight_elicitations"


def upgrade() -> None:
    # `sqlite_sequence` is seeded at the current max automatically: the batch recreate
    # copies every row across with its explicit `id`, matching a fresh store row-for-row.
    with op.batch_alter_table(_TABLE, recreate="always", table_kwargs={"sqlite_autoincrement": True}):
        pass


def downgrade() -> None:
    # Strips the AUTOINCREMENT keyword back off — `table_kwargs` defaults to none, and a
    # plain `INTEGER PRIMARY KEY` is what reflection restores everything else from.
    with op.batch_alter_table(_TABLE, recreate="always"):
        pass

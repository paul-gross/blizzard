"""transcript payload bytes — a ``transcript_outbound_buffer.payload_bytes``: each buffered payload's UTF-8 byte
length, ``NOT NULL`` with a server default of 0. Every existing row, pending or acked, is filled from
``length(payload)`` in bounded pages; the payloads are ASCII-escaped JSON, so their character length is their byte
length. No other table changes, and a rerun skips the column and every row already filled. ``downgrade()`` drops the
column.

Revision ID: 20261124_1000_transcript_payload_bytes
Revises: 20261120_1000_host_records
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20261124_1000_transcript_payload_bytes"
down_revision: str | None = "20261120_1000_host_records"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_BATCH_ROWS = 1000
_TABLE = "transcript_outbound_buffer"
_COLUMN = "payload_bytes"

#: The buffer frozen to the columns this revision pages by and fills.
_buffer = sa.Table(
    _TABLE,
    sa.MetaData(),
    sa.Column("runner_id", sa.String(), primary_key=True),
    sa.Column("seq", sa.Integer(), primary_key=True),
    sa.Column("payload", sa.Text(), nullable=False),
    sa.Column(_COLUMN, sa.Integer(), nullable=False),
)


def _columns(bind: sa.Connection) -> set[str]:
    return {c["name"] for c in sa.inspect(bind).get_columns(_TABLE)}


def _fill(bind: sa.Connection) -> None:
    """Each row still at the default takes its payload's length, one bounded page per runner at a time."""
    unfilled = (_buffer.c[_COLUMN] == 0) & (sa.func.length(_buffer.c.payload) > 0)
    page = (
        sa.select(_buffer.c.runner_id, _buffer.c.seq)
        .where(unfilled)
        .order_by(_buffer.c.runner_id, _buffer.c.seq)
        .limit(_BATCH_ROWS)
    )
    while keys := list(bind.execute(page)):
        runner_id = keys[0].runner_id
        seqs = [key.seq for key in keys if key.runner_id == runner_id]
        bind.execute(
            sa.update(_buffer)
            .where(_buffer.c.runner_id == runner_id, _buffer.c.seq.in_(seqs))
            .values({_COLUMN: sa.func.length(_buffer.c.payload)})
        )


def upgrade() -> None:
    bind = op.get_bind()
    if _COLUMN not in _columns(bind):
        op.add_column(_TABLE, sa.Column(_COLUMN, sa.Integer(), nullable=False, server_default="0"))
    _fill(bind)


def downgrade() -> None:
    if _COLUMN in _columns(op.get_bind()):
        op.drop_column(_TABLE, _COLUMN)

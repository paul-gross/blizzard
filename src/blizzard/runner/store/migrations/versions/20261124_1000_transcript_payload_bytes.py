"""transcript payload bytes — ``transcript_outbound_buffer.payload_bytes``, ``NOT NULL`` default 0, each existing row
filled from ``length(payload)`` (ASCII-escaped JSON) in ``(runner_id, seq)`` keyset pages; a rerun skips filled rows.

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
    """Each row still at the default takes its payload's length, one bounded page at a time, each page starting past
    the last key the one before it read."""
    unfilled = (_buffer.c[_COLUMN] == 0) & (sa.func.length(_buffer.c.payload) > 0)
    page = sa.select(_buffer.c.runner_id, _buffer.c.seq).order_by(_buffer.c.runner_id, _buffer.c.seq).limit(_BATCH_ROWS)
    after: sa.ColumnElement[bool] = sa.true()
    while keys := list(bind.execute(page.where(unfilled, after))):
        seqs_by_runner: dict[str, list[int]] = {}
        for key in keys:
            seqs_by_runner.setdefault(key.runner_id, []).append(key.seq)
        for runner_id, seqs in seqs_by_runner.items():
            bind.execute(
                sa.update(_buffer)
                .where(_buffer.c.runner_id == runner_id, _buffer.c.seq.in_(seqs))
                .values({_COLUMN: sa.func.length(_buffer.c.payload)})
            )
        last = keys[-1]
        after = (_buffer.c.runner_id > last.runner_id) | (
            (_buffer.c.runner_id == last.runner_id) & (_buffer.c.seq > last.seq)
        )


def upgrade() -> None:
    bind = op.get_bind()
    if _COLUMN not in _columns(bind):
        op.add_column(_TABLE, sa.Column(_COLUMN, sa.Integer(), nullable=False, server_default="0"))
    _fill(bind)


def downgrade() -> None:
    if _COLUMN in _columns(op.get_bind()):
        op.drop_column(_TABLE, _COLUMN)

"""Epoch owners — one owner per fencing epoch, backfilled — and the lease a mint named.

Every restart epoch becomes hub-owned; every epoch still unowned then takes the runner of
its earliest ``lease_facts`` row, a ``runner_id`` of ``hub`` meaning hub-owned.
``downgrade()`` drops the table and the column, and the owners with them.

Revision ID: 20260930_1000_epoch_owners
Revises: 20260929_1100_drop_open_pr_facts
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime

import sqlalchemy as sa
from alembic import op

revision: str = "20260930_1000_epoch_owners"
down_revision: str | None = "20260929_1100_drop_open_pr_facts"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_OWNERS = "epoch_owners"
_LEASES = "lease_facts"
_LEASE_ID = "lease_id"
_HUB_RUNNER_ID = "hub"

# Frozen literals (``bzh:frozen-revisions``): the created table as it stands here, and narrow
# read stubs of the two tables the backfill selects from.
_frozen = sa.MetaData()
sa.Table("chunks", _frozen, sa.Column("chunk_id", sa.String, primary_key=True))
epoch_owners = sa.Table(
    _OWNERS,
    _frozen,
    sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
    sa.Column("chunk_id", sa.String, sa.ForeignKey("chunks.chunk_id"), nullable=False),
    sa.Column("epoch", sa.Integer, nullable=False),
    sa.Column("runner_id", sa.String, nullable=True),
    sa.Column("recorded_at", sa.DateTime, nullable=False),
    sa.UniqueConstraint("chunk_id", "epoch", name="uq_epoch_owners_chunk_id_epoch"),
)
_lease_facts = sa.Table(
    _LEASES,
    _frozen,
    sa.Column("id", sa.Integer),
    sa.Column("chunk_id", sa.String),
    sa.Column("epoch", sa.Integer),
    sa.Column("runner_id", sa.String),
    sa.Column("minted_at", sa.DateTime),
)
_chunk_restarts = sa.Table(
    "chunk_restarts",
    _frozen,
    sa.Column("id", sa.Integer),
    sa.Column("chunk_id", sa.String),
    sa.Column("epoch", sa.Integer),
    sa.Column("recorded_at", sa.DateTime),
)


def _has_column(bind: sa.Connection, table: str, column: str) -> bool:
    return column in {c["name"] for c in sa.inspect(bind).get_columns(table)}


def _owners(bind: sa.Connection) -> list[dict[str, object]]:
    """Restart epochs first, all hub-owned; then each remaining epoch's earliest lease row."""
    owned: dict[tuple[str, int], dict[str, object]] = {}
    restarts = bind.execute(
        sa.select(_chunk_restarts.c.chunk_id, _chunk_restarts.c.epoch, _chunk_restarts.c.recorded_at).order_by(
            _chunk_restarts.c.id
        )
    ).all()
    for chunk_id, epoch, recorded_at in restarts:
        owned.setdefault((chunk_id, epoch), _row(chunk_id, epoch, None, recorded_at))
    leases = bind.execute(
        sa.select(
            _lease_facts.c.chunk_id, _lease_facts.c.epoch, _lease_facts.c.runner_id, _lease_facts.c.minted_at
        ).order_by(_lease_facts.c.id)
    ).all()
    for chunk_id, epoch, runner_id, minted_at in leases:
        owner = None if runner_id == _HUB_RUNNER_ID else runner_id
        owned.setdefault((chunk_id, epoch), _row(chunk_id, epoch, owner, minted_at))
    return list(owned.values())


def _row(chunk_id: str, epoch: int, runner_id: str | None, at: datetime) -> dict[str, object]:
    return {"chunk_id": chunk_id, "epoch": epoch, "runner_id": runner_id, "recorded_at": at}


def upgrade() -> None:
    bind = op.get_bind()
    if not _has_column(bind, _LEASES, _LEASE_ID):
        op.add_column(_LEASES, sa.Column(_LEASE_ID, sa.String(), nullable=True))
    if _OWNERS in sa.inspect(bind).get_table_names():
        return
    epoch_owners.create(bind)
    rows = _owners(bind)
    if rows:
        bind.execute(epoch_owners.insert(), rows)


def downgrade() -> None:
    bind = op.get_bind()
    if _OWNERS in sa.inspect(bind).get_table_names():
        epoch_owners.drop(bind)
    if _has_column(bind, _LEASES, _LEASE_ID):
        with op.batch_alter_table(_LEASES) as batch:
            batch.drop_column(_LEASE_ID)

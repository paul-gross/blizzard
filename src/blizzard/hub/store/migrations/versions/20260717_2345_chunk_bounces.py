"""delivery kick-back bounces + per-node bounce cap (issue #64, hub store tree)

A delivery kick-back (conflict / CI-red / master-moved) is contention, not failure
(#64): it consumes no node retry and triggers no escalation by itself. This revision
adds:

* ``chunk_bounces`` — one append-only row per kick-back, stamped from the injected
  clock and natural-keyed on ``(chunk_id, epoch)`` (the coordinator's own ``hub_epoch``)
  so a redelivery replay after a crash never double-counts a bounce. ``bounce_count``
  (``hub/domain/work.py``) derives purely from these rows.
* ``graph_nodes.bounce_cap`` — a per-node override of the fleet-wide default
  (``graph.DEFAULT_BOUNCE_CAP``); null accepts the default.

Both are brand new as of this revision — no later revision reshapes either yet — so
both are imported directly from ``schema.py`` rather than frozen locally (the exception
``20260714_0819_hub_delivery_pr_facts`` documents for a table no later revision touches).

Revision ID: 20260717_2345_hub_chunk_bounces
Revises: 20260718_0930_hub_runner_local_pause_reason
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from blizzard.hub.store.schema import chunk_bounces

revision: str = "20260717_2345_hub_chunk_bounces"
down_revision: str | None = "20260718_0930_hub_runner_local_pause_reason"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLE = "graph_nodes"
_COLUMN = "bounce_cap"


def _has_column(bind: sa.Connection) -> bool:
    return _COLUMN in {c["name"] for c in sa.inspect(bind).get_columns(_TABLE)}


def upgrade() -> None:
    bind = op.get_bind()
    chunk_bounces.create(bind, checkfirst=True)
    if not _has_column(bind):
        op.add_column(_TABLE, sa.Column(_COLUMN, sa.Integer(), nullable=True))


def downgrade() -> None:
    bind = op.get_bind()
    if _has_column(bind):
        op.drop_column(_TABLE, _COLUMN)
    chunk_bounces.drop(bind, checkfirst=True)

"""pending-poll outcome: hub_node_poll + per-node poll cadence (issue #66, hub store tree)

A hub command node whose ``run:`` step reports the reserved ``pending`` outcome (#66)
records no transition — it appends a poll-attempt fact instead, releases the fleet-wide
``hub_exec_slot`` immediately, and is re-run once ``poll_interval`` has elapsed. This
revision adds:

* ``hub_node_poll`` — one append-only row per poll attempt, stamped from the injected
  clock. Pending-ness (``hub_node_pending``, ``hub/domain/work.py``) derives purely from
  these rows plus the transition table, so a ``kill -9`` between polls resumes polling
  from the store with nothing lost.
* ``graph_nodes.poll_interval_seconds`` / ``graph_nodes.poll_timeout_seconds`` — a
  per-node override of the executor's own defaults; null accepts them.

Both are brand new as of this revision — no later revision reshapes either yet — so
``hub_node_poll`` is imported directly from ``schema.py`` (the same exception
``chunk_bounces``'s own migration documents).

Revision ID: 20260718_0030_hub_node_poll
Revises: 20260717_2359_hub_command_nodes
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from blizzard.hub.store.schema import hub_node_poll

revision: str = "20260718_0030_hub_node_poll"
down_revision: str | None = "20260717_2359_hub_command_nodes"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLE = "graph_nodes"
_COLUMNS = ("poll_interval_seconds", "poll_timeout_seconds")


def _existing_columns(bind: sa.Connection) -> set[str]:
    return {c["name"] for c in sa.inspect(bind).get_columns(_TABLE)}


def upgrade() -> None:
    bind = op.get_bind()
    existing = _existing_columns(bind)
    for column in _COLUMNS:
        if column not in existing:
            op.add_column(_TABLE, sa.Column(column, sa.Integer(), nullable=True))
    hub_node_poll.create(bind, checkfirst=True)


def downgrade() -> None:
    bind = op.get_bind()
    hub_node_poll.drop(bind, checkfirst=True)
    existing = _existing_columns(bind)
    for column in _COLUMNS:
        if column in existing:
            op.drop_column(_TABLE, column)

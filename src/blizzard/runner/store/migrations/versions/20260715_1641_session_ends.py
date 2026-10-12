"""session-end signal fact table (runner store tree) — ``session_ends`` records the
durable "the worker declared done" fact whose *absence* marks a crash.

Revision ID: 20260715_1641_runner_session_ends
Revises: 20260715_1633_runner_workspace_prompt
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from blizzard.foundation.store.utc import UtcDateTime


revision: str = "20260715_1641_runner_session_ends"
down_revision: str | None = "20260715_1633_runner_workspace_prompt"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_frozen_metadata = sa.MetaData()
_session_ends = sa.Table(
    "session_ends",
    _frozen_metadata,
    sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
    sa.Column("lease_id", sa.String(), nullable=False),
    sa.Column("ended_at", UtcDateTime(), nullable=False),
)


def upgrade() -> None:
    _session_ends.create(op.get_bind(), checkfirst=True)


def downgrade() -> None:
    _session_ends.drop(op.get_bind(), checkfirst=True)

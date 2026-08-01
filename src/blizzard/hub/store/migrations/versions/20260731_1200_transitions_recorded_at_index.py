"""transitions.recorded_at index — the activity feed's bounded read (issue #213, hub store tree)

The board's Event log backfill (issue #213) reads several fact tables bounded by
``WHERE recorded_at >= :since ORDER BY recorded_at DESC, <pk> DESC LIMIT :limit`` —
never a full-table scan. ``event_log`` already carries ``ix_event_log_recorded_at``
(20260721_1600_hub_event_log); a few FK/token columns are indexed elsewhere
(20260718_1130_hub_runner_token). ``transitions`` is the one **high-volume** fact table
in that bounded-read set with no timestamp index at all — every chunk's every node-step
lands a row here, unlike the low-volume tables (``escalations``, ``requeues``, ...) a
full scan is fine against. This revision closes that gap alone: index-only, no column,
mirroring ``20260718_1130_hub_runner_token``'s plain-string-names style (not the
imported, frozen ``transitions`` table object — this index rides no table creation).

Idempotent like that revision: the index is created/dropped only where an older
database lacks/has it, so a fresh ``base -> head`` and an in-place upgrade both
converge, and ``downgrade()`` genuinely reverses ``upgrade()``.

Revision ID: 20260731_1200_hub_transitions_recorded_at
Revises: 20260728_1410_hub_chunk_defaults
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260731_1200_hub_transitions_recorded_at"
down_revision: str | None = "20260728_1410_hub_chunk_defaults"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLE = "transitions"
_COLUMN = "recorded_at"
_INDEX = "ix_transitions_recorded_at"


def _has_index(bind: sa.Connection) -> bool:
    return _INDEX in {i["name"] for i in sa.inspect(bind).get_indexes(_TABLE)}


def upgrade() -> None:
    bind = op.get_bind()
    if not _has_index(bind):
        op.create_index(_INDEX, _TABLE, [_COLUMN])


def downgrade() -> None:
    bind = op.get_bind()
    if _has_index(bind):
        op.drop_index(_INDEX, table_name=_TABLE)

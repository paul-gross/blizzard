"""transcript segments (blizzard#247, epic:transcripts, hub store tree) — the append-only
per-record transcript table and its lane's own high-water table. Creates both, ``checkfirst``.

Revision ID: 20260809_1200_hub_transcript_segments
Revises: 20260803_1000_hub_escalation_wrapped_takeover
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

from blizzard.hub.store.schema import transcript_high_water, transcript_segments

revision: str = "20260809_1200_hub_transcript_segments"
down_revision: str | None = "20260803_1000_hub_escalation_wrapped_takeover"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLES = (transcript_segments, transcript_high_water)


def upgrade() -> None:
    bind = op.get_bind()
    for table in _TABLES:
        table.create(bind, checkfirst=True)


def downgrade() -> None:
    bind = op.get_bind()
    for table in reversed(_TABLES):
        table.drop(bind, checkfirst=True)

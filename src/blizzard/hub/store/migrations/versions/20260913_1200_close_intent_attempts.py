"""close_intent_attempts — the close-drain sweep's backoff ledger (blizzard#524 D7). One
new table; no backfill, since a table with no rows starts every existing pending intent
immediately due, exactly like a never-attempted one.

Revision ID: 20260913_1200_close_intent_attempts
Revises: 20260913_1000_transcript_segments_content_digest
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

from blizzard.hub.store.schema import close_intent_attempts

revision: str = "20260913_1200_close_intent_attempts"
down_revision: str | None = "20260913_1000_transcript_segments_content_digest"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLES = (close_intent_attempts,)


def upgrade() -> None:
    bind = op.get_bind()
    for table in _TABLES:
        table.create(bind, checkfirst=True)


def downgrade() -> None:
    bind = op.get_bind()
    for table in reversed(_TABLES):
        table.drop(bind, checkfirst=True)

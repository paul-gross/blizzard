"""context samples — one append-only row per sampled reading of a running lease's session
context, the observation lane behind the configured warn line (runner store tree)

Revision ID: 20260811_1000_runner_context_samples
Revises: 20260811_0900_runner_transcript_segment_supersedes
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

from blizzard.runner.store.schema import context_samples

revision: str = "20260811_1000_runner_context_samples"
down_revision: str | None = "20260811_0900_runner_transcript_segment_supersedes"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLES = (context_samples,)


def upgrade() -> None:
    bind = op.get_bind()
    for table in _TABLES:
        table.create(bind, checkfirst=True)


def downgrade() -> None:
    bind = op.get_bind()
    for table in reversed(_TABLES):
        table.drop(bind, checkfirst=True)

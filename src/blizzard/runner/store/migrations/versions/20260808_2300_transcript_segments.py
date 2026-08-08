"""transcript segments and their own outbound buffer — the dedicated transcript lane
(runner store tree, issue #246)

Revision ID: 20260808_2300_runner_transcript_segments
Revises: 20260801_1500_runner_external_usage_samples
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

from blizzard.runner.store.schema import transcript_outbound_buffer, transcript_segments

revision: str = "20260808_2300_runner_transcript_segments"
down_revision: str | None = "20260801_1500_runner_external_usage_samples"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLES = (transcript_segments, transcript_outbound_buffer)


def upgrade() -> None:
    bind = op.get_bind()
    for table in _TABLES:
        table.create(bind, checkfirst=True)


def downgrade() -> None:
    bind = op.get_bind()
    for table in reversed(_TABLES):
        table.drop(bind, checkfirst=True)

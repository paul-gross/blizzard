"""secrets, secret_lifecycle_facts — the write-only secret store, created ``checkfirst``.

Revision ID: 20261004_1000_hub_secrets
Revises: 20261003_1300_hub_transcript_segment_spawn_cwd
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

from blizzard.hub.store.schema import secret_lifecycle_facts, secrets

revision: str = "20261004_1000_hub_secrets"
down_revision: str | None = "20261003_1300_hub_transcript_segment_spawn_cwd"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLES = (secrets, secret_lifecycle_facts)


def upgrade() -> None:
    bind = op.get_bind()
    for table in _TABLES:
        table.create(bind, checkfirst=True)


def downgrade() -> None:
    bind = op.get_bind()
    for table in reversed(_TABLES):
        table.drop(bind, checkfirst=True)

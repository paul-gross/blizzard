"""secrets, secret_lifecycle_facts — the write-only secret store, created ``checkfirst``.

Revision ID: 20261004_1000_hub_secrets
Revises: 20261003_1400_escalation_cause
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from blizzard.foundation.store.utc import UtcDateTime

revision: str = "20261004_1000_hub_secrets"
down_revision: str | None = "20261003_1400_escalation_cause"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Frozen (``bzh:frozen-revisions``).
_frozen_metadata = sa.MetaData()
_secrets = sa.Table(
    "secrets",
    _frozen_metadata,
    sa.Column("name", sa.String, primary_key=True),
    sa.Column("ciphertext", sa.Text, nullable=False),
    sa.Column("nonce", sa.Text, nullable=False),
    sa.Column("key_id", sa.String, nullable=False),
    sa.Column("revision", sa.Integer, nullable=False),
    sa.Column("replaced_at", UtcDateTime, nullable=False),
    sa.Column("replaced_by", sa.String, nullable=False),
    sa.Column("created_at", UtcDateTime, nullable=False),
)
_secret_lifecycle_facts = sa.Table(
    "secret_lifecycle_facts",
    _frozen_metadata,
    sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
    sa.Column("name", sa.String, sa.ForeignKey("secrets.name"), nullable=False),
    sa.Column("retired", sa.Boolean, nullable=False),
    sa.Column("set_at", UtcDateTime, nullable=False),
    sa.Column("set_by", sa.String, nullable=False),
)

_TABLES = (_secrets, _secret_lifecycle_facts)


def upgrade() -> None:
    bind = op.get_bind()
    for table in _TABLES:
        table.create(bind, checkfirst=True)


def downgrade() -> None:
    bind = op.get_bind()
    for table in reversed(_TABLES):
        table.drop(bind, checkfirst=True)

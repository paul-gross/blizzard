"""work_sources, work_source_lifecycle_facts, config_changes — configured work sources and the change log.

Revision ID: 20261004_1100_work_source_records
Revises: 20261004_1000_hub_secrets
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from blizzard.foundation.store.utc import UtcDateTime

revision: str = "20261004_1100_work_source_records"
down_revision: str | None = "20261004_1000_hub_secrets"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Frozen (``bzh:frozen-revisions``); ``secrets`` is an FK stub.
_frozen_metadata = sa.MetaData()
sa.Table(
    "secrets",
    _frozen_metadata,
    sa.Column("name", sa.String, primary_key=True),
)
_work_sources = sa.Table(
    "work_sources",
    _frozen_metadata,
    sa.Column("name", sa.String, primary_key=True),
    sa.Column("provider", sa.String, nullable=False),
    sa.Column("locator", sa.String, nullable=False),
    sa.Column("api_base", sa.String, nullable=True),
    sa.Column("web_base", sa.String, nullable=True),
    sa.Column("annotate", sa.Boolean, nullable=False),
    sa.Column("secret_name", sa.String, sa.ForeignKey("secrets.name"), nullable=True),
    sa.Column("revision", sa.Integer, nullable=False),
    sa.Column("created_at", UtcDateTime, nullable=False),
    sa.Column("created_by", sa.String, nullable=False),
    sa.UniqueConstraint("provider", "locator", name="uq_work_sources_provider_locator"),
)
_work_source_lifecycle_facts = sa.Table(
    "work_source_lifecycle_facts",
    _frozen_metadata,
    sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
    sa.Column("name", sa.String, sa.ForeignKey("work_sources.name"), nullable=False),
    sa.Column("retired", sa.Boolean, nullable=False),
    sa.Column("set_at", UtcDateTime, nullable=False),
    sa.Column("set_by", sa.String, nullable=False),
)
_config_changes = sa.Table(
    "config_changes",
    _frozen_metadata,
    sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
    sa.Column("recorded_at", UtcDateTime, nullable=False),
    sa.Column("actor", sa.String, nullable=False),
    sa.Column("door", sa.String, nullable=False),
    sa.Column("record_kind", sa.String, nullable=False),
    sa.Column("record_key", sa.String, nullable=False),
    sa.Column("revision", sa.Integer, nullable=False),
    sa.Column("op", sa.String, nullable=False),
    sa.Column("diff", sa.Text, nullable=False),
    sa.Column("apply_id", sa.String, nullable=True),
)
sa.Index("ix_config_changes_record", _config_changes.c.record_kind, _config_changes.c.record_key, _config_changes.c.id)

_TABLES = (_work_sources, _work_source_lifecycle_facts, _config_changes)


def upgrade() -> None:
    bind = op.get_bind()
    for table in _TABLES:
        table.create(bind, checkfirst=True)


def downgrade() -> None:
    bind = op.get_bind()
    for table in reversed(_TABLES):
        table.drop(bind, checkfirst=True)

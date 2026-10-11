"""tenants — the tenant record, the hub-level tables beside it, and the first tenant.

Creates ``tenants``, ``tenant_deletions``, ``invitations``, ``invitation_facts`` and ``hub_event_log``, and inserts
the one tenant every existing row is carried into: a fresh ``ten_`` id, named ``default``, created by ``migration``.
A fresh store gets the same tenant, empty. No existing table changes shape.

Re-running the upgrade creates only what is missing and inserts no second tenant; the downgrade drops exactly the five
tables, the first tenant with them.
Revision ID: 20261008_1100_tenants
Revises: 20261008_1000_drop_work_item_proposals
"""

from __future__ import annotations

import os
from collections.abc import Sequence
from datetime import datetime

import sqlalchemy as sa
from alembic import op

from blizzard.foundation.store.migrations import migration_clock
from blizzard.foundation.store.utc import UtcDateTime

revision: str = "20261008_1100_tenants"
down_revision: str | None = "20261008_1000_drop_work_item_proposals"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Restated, not imported, from ``blizzard.foundation.ids`` (``bzh:frozen-revisions``).
_TENANT_PREFIX = "ten"
_CROCKFORD = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"
_ULID_CHARS = 26  # 48 bits of millisecond timestamp, then 80 random bits
_RANDOM_BITS = 80

_FIRST_TENANT_NAME = "default"
_FIRST_TENANT_CREATED_BY = "migration"

_metadata = sa.MetaData()

_tenants = sa.Table(
    "tenants",
    _metadata,
    sa.Column("tenant_id", sa.String, primary_key=True),
    sa.Column("name", sa.String, nullable=False),
    sa.Column("created_at", UtcDateTime, nullable=False),
    sa.Column("created_by", sa.String, nullable=False),
)

_tenant_deletions = sa.Table(
    "tenant_deletions",
    _metadata,
    sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
    sa.Column("tenant_id", sa.String, nullable=False),
    sa.Column("name", sa.String, nullable=False),
    sa.Column("deleted_at", UtcDateTime, nullable=True),
    sa.Column("deleted_by", sa.String, nullable=False),
    sa.Column("rows_removed", sa.Integer, nullable=True),
)

_invitations = sa.Table(
    "invitations",
    _metadata,
    sa.Column("invitation_id", sa.String, primary_key=True),
    sa.Column(
        "tenant_id", sa.String, sa.ForeignKey("tenants.tenant_id", name="fk_invitations_tenant_id"), nullable=False
    ),
    sa.Column("email", sa.String, nullable=False),
    sa.Column("role", sa.String, nullable=False),
    sa.Column("token_hash", sa.String, nullable=False),
    sa.Column("created_by", sa.String, nullable=False),
    sa.Column("created_at", UtcDateTime, nullable=False),
    sa.Column("expires_at", UtcDateTime, nullable=False),
)

_invitation_facts = sa.Table(
    "invitation_facts",
    _metadata,
    sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
    sa.Column(
        "tenant_id", sa.String, sa.ForeignKey("tenants.tenant_id", name="fk_invitation_facts_tenant_id"), nullable=False
    ),
    sa.Column(
        "invitation_id",
        sa.String,
        sa.ForeignKey("invitations.invitation_id", name="fk_invitation_facts_invitation_id"),
        nullable=False,
    ),
    sa.Column("kind", sa.String, nullable=False),
    sa.Column("actor_user_id", sa.String, nullable=False),
    sa.Column("recorded_at", UtcDateTime, nullable=False),
    sa.UniqueConstraint("invitation_id", name="uq_invitation_facts_invitation_id"),
    sa.CheckConstraint("kind IN ('accepted', 'revoked')", name="ck_invitation_facts_kind"),
)

_hub_event_log = sa.Table(
    "hub_event_log",
    _metadata,
    sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
    sa.Column("recorded_at", UtcDateTime, nullable=False),
    sa.Column("severity", sa.String, nullable=False),
    sa.Column("kind", sa.String, nullable=False),
    sa.Column("message", sa.Text, nullable=False),
    sa.Column("detail", sa.Text, nullable=True),
)

# Attached to their tables, so each is created with its table.
sa.Index("ix_tenant_deletions_tenant_id", _tenant_deletions.c.tenant_id)
sa.Index("ix_invitations_tenant_id", _invitations.c.tenant_id)
sa.Index("ix_invitations_token_hash", _invitations.c.token_hash)
sa.Index("ix_invitation_facts_tenant_id", _invitation_facts.c.tenant_id)
sa.Index("ix_hub_event_log_recorded_at", _hub_event_log.c.recorded_at)

# Parents before children; the downgrade drops them in reverse.
_TABLES: tuple[sa.Table, ...] = (_tenants, _tenant_deletions, _invitations, _invitation_facts, _hub_event_log)


def _encode(value: int) -> str:
    chars = []
    for _ in range(_ULID_CHARS):
        value, rem = divmod(value, 32)
        chars.append(_CROCKFORD[rem])
    return "".join(reversed(chars))


def _mint_tenant_id(at: datetime) -> str:
    millis = int(at.timestamp() * 1000)
    randomness = int.from_bytes(os.urandom(_RANDOM_BITS // 8), "big")
    return f"{_TENANT_PREFIX}_{_encode((millis << _RANDOM_BITS) | randomness)}"


def _index_names(bind: sa.Connection, table: str) -> set[str]:
    return {str(index["name"]) for index in sa.inspect(bind).get_indexes(table)}


def upgrade() -> None:
    bind = op.get_bind()
    for table in _TABLES:
        table.create(bind, checkfirst=True)  # a table created here brings its indexes
        present = _index_names(bind, table.name)
        for index in table.indexes:
            if index.name not in present:
                index.create(bind)
    if bind.execute(sa.select(sa.func.count()).select_from(_tenants)).scalar_one() == 0:
        now = migration_clock().now()
        bind.execute(
            _tenants.insert().values(
                tenant_id=_mint_tenant_id(now),
                name=_FIRST_TENANT_NAME,
                created_at=now,
                created_by=_FIRST_TENANT_CREATED_BY,
            )
        )


def downgrade() -> None:
    bind = op.get_bind()
    for table in reversed(_TABLES):
        table.drop(bind, checkfirst=True)

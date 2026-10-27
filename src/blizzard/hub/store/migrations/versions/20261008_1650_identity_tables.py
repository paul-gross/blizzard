"""identity tables — ``membership_facts`` and ``hub_admin_facts``, the facts roles and hub administrators live in.

Creates both tables, each with its indexes, and nothing else: no row is written and no existing table changes shape.
A membership grants one user one role in one tenant; a hub-administrator fact grants or revokes the role above every
tenant. Both are global, read before any tenant is known. Re-running the upgrade creates only what is missing.

The downgrade refuses while more than one tenant exists, naming them; with one it drops both tables with every fact
they hold.
Revision ID: 20261008_1650_identity_tables
Revises: 20261008_1600_hub_event_log_move
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from blizzard.foundation.store.utc import UtcDateTime

revision: str = "20261008_1650_identity_tables"
down_revision: str | None = "20261008_1600_hub_event_log_move"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_metadata = sa.MetaData()
# FK stubs (``bzh:frozen-revisions``).
_tenants = sa.Table("tenants", _metadata, sa.Column("tenant_id", sa.String, primary_key=True))
sa.Table("users", _metadata, sa.Column("id", sa.String, primary_key=True))
_membership_facts = sa.Table(
    "membership_facts",
    _metadata,
    sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
    sa.Column(
        "tenant_id", sa.String, sa.ForeignKey("tenants.tenant_id", name="fk_membership_facts_tenant_id"), nullable=False
    ),
    sa.Column("user_id", sa.String, sa.ForeignKey("users.id", name="fk_membership_facts_user_id"), nullable=False),
    sa.Column("role", sa.String, nullable=True),
    sa.Column("set_at", UtcDateTime, nullable=False),
    sa.Column("set_by", sa.String, nullable=False),
)
_hub_admin_facts = sa.Table(
    "hub_admin_facts",
    _metadata,
    sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
    sa.Column("user_id", sa.String, sa.ForeignKey("users.id", name="fk_hub_admin_facts_user_id"), nullable=False),
    sa.Column("granted", sa.Boolean, nullable=False),
    sa.Column("set_at", UtcDateTime, nullable=False),
    sa.Column("set_by", sa.String, nullable=False),
)
# Attached to their tables, so each is created with its table.
sa.Index(
    "ix_membership_facts_tenant_user",
    _membership_facts.c.tenant_id,
    _membership_facts.c.user_id,
    _membership_facts.c.id,
)
sa.Index("ix_membership_facts_user_id", _membership_facts.c.user_id)
sa.Index("ix_hub_admin_facts_user_id", _hub_admin_facts.c.user_id)

_TABLES: tuple[sa.Table, ...] = (_membership_facts, _hub_admin_facts)


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


def downgrade() -> None:
    bind = op.get_bind()
    tenants = list(bind.execute(sa.select(_tenants.c.tenant_id).order_by(_tenants.c.tenant_id)).scalars())
    if len(tenants) > 1:
        raise RuntimeError(f"delete every tenant but one before downgrading; the store holds {', '.join(tenants)}")
    for table in reversed(_TABLES):
        table.drop(bind, checkfirst=True)

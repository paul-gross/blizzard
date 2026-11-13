"""credentials — the credential record's tables, and a nullable ``credential_id`` on its referrers.

Creates ``credentials``, ``credential_former_slugs`` and ``credential_lifecycle_facts``, and adds a nullable
``credential_id`` with no foreign key to ``repositories`` and ``work_sources``; no row is written. Every step inspects
first and skips what a stopped run already did. The downgrade drops both columns and the three tables.
Revision ID: 20261113_0900_credentials
Revises: 20261113_0900_repair_hub_locks
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from blizzard.foundation.store.utc import UtcDateTime

revision: str = "20261113_0900_credentials"
down_revision: str | None = "20261113_0900_repair_hub_locks"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TENANT = "tenant_id"
_CREDENTIAL_ID = "credential_id"
_CREDENTIALS = "credentials"
_FORMER_SLUGS = "credential_former_slugs"
_FACTS = "credential_lifecycle_facts"
_REFERRERS = ("repositories", "work_sources")
_CREDENTIAL_KEY = ["credentials.tenant_id", "credentials.credential_id"]


def _create_tables(bind: sa.Connection) -> None:
    inspector = sa.inspect(bind)
    if not inspector.has_table(_CREDENTIALS):
        op.create_table(
            _CREDENTIALS,
            sa.Column(_CREDENTIAL_ID, sa.String(), nullable=False),
            sa.Column("slug", sa.String(), nullable=False),
            sa.Column("name", sa.String(), nullable=False),
            sa.Column("forge_id", sa.String(), nullable=False),
            sa.Column("binding", sa.String(), nullable=False),
            sa.Column("secret_name", sa.String(), nullable=False),
            sa.Column("settings", sa.Text(), nullable=False),
            sa.Column("authority", sa.String(), nullable=False),
            sa.Column("revision", sa.Integer(), nullable=False),
            sa.Column("created_at", UtcDateTime(), nullable=False),
            sa.Column("created_by", sa.String(), nullable=False),
            sa.Column(_TENANT, sa.String(), nullable=False),
            sa.PrimaryKeyConstraint(_TENANT, _CREDENTIAL_ID, name="pk_credentials"),
            sa.UniqueConstraint(_TENANT, "slug", name="uq_credentials_tenant_slug"),
            sa.UniqueConstraint(_TENANT, "forge_id", "authority", name="uq_credentials_tenant_forge_authority"),
            sa.ForeignKeyConstraint(["forge_id"], ["forges.forge_id"], name="fk_credentials_forges"),
            sa.ForeignKeyConstraint([_TENANT], ["tenants.tenant_id"], name="fk_credentials_tenant_id"),
            sa.ForeignKeyConstraint(
                [_TENANT, "secret_name"], ["secrets.tenant_id", "secrets.name"], name="fk_credentials_secrets"
            ),
        )
    if not inspector.has_table(_FORMER_SLUGS):
        op.create_table(
            _FORMER_SLUGS,
            sa.Column("slug", sa.String(), nullable=False),
            sa.Column(_CREDENTIAL_ID, sa.String(), nullable=False),
            sa.Column("left_at", UtcDateTime(), nullable=False),
            sa.Column(_TENANT, sa.String(), nullable=False),
            sa.PrimaryKeyConstraint(_TENANT, "slug", name="pk_credential_former_slugs"),
            sa.ForeignKeyConstraint([_TENANT], ["tenants.tenant_id"], name="fk_credential_former_slugs_tenant_id"),
            sa.ForeignKeyConstraint(
                [_TENANT, _CREDENTIAL_ID], _CREDENTIAL_KEY, name="fk_credential_former_slugs_credentials"
            ),
        )
    if not inspector.has_table(_FACTS):
        op.create_table(
            _FACTS,
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column(_CREDENTIAL_ID, sa.String(), nullable=False),
            sa.Column("retired", sa.Boolean(), nullable=False),
            sa.Column("set_at", UtcDateTime(), nullable=False),
            sa.Column("set_by", sa.String(), nullable=False),
            sa.Column(_TENANT, sa.String(), nullable=False),
            sa.ForeignKeyConstraint([_TENANT], ["tenants.tenant_id"], name="fk_credential_lifecycle_facts_tenant_id"),
            sa.ForeignKeyConstraint(
                [_TENANT, _CREDENTIAL_ID], _CREDENTIAL_KEY, name="fk_credential_lifecycle_facts_credentials"
            ),
        )
        op.create_index("ix_credential_lifecycle_facts_tenant_id", _FACTS, [_TENANT])


def _columns(bind: sa.Connection, table: str) -> set[str]:
    return {str(c["name"]) for c in sa.inspect(bind).get_columns(table)}


def _add_columns(bind: sa.Connection) -> None:
    """Add the nullable ``credential_id`` to both referring tables — in place on every dialect."""
    for table in _REFERRERS:
        if _CREDENTIAL_ID in _columns(bind, table):
            continue
        with op.batch_alter_table(table, recreate="auto") as batch:
            batch.add_column(sa.Column(_CREDENTIAL_ID, sa.String(), nullable=True))


def upgrade() -> None:
    bind = op.get_bind()
    _create_tables(bind)
    _add_columns(bind)


def _clear_stale_copy(bind: sa.Connection, table: str) -> None:
    """Drop the copy a batch left behind when its run stopped part way (SQLite makes it outside the batch's
    transaction)."""
    copy = f"_alembic_tmp_{table}"[:50]
    if sa.inspect(bind).has_table(copy):
        op.drop_table(copy)


def downgrade() -> None:
    bind = op.get_bind()
    for table in _REFERRERS:
        if _CREDENTIAL_ID not in _columns(bind, table):
            continue
        _clear_stale_copy(bind, table)
        # The table as the store holds it: a batch left to reflect for itself drops an unnamed CHECK constraint.
        reflected = sa.Table(table, sa.MetaData(), autoload_with=bind)
        with op.batch_alter_table(table, recreate="auto", copy_from=reflected) as batch:
            batch.drop_column(_CREDENTIAL_ID)
    for table in (_FACTS, _FORMER_SLUGS, _CREDENTIALS):
        if sa.inspect(bind).has_table(table):
            op.drop_table(table)

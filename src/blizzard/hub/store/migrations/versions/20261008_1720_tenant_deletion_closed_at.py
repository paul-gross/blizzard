"""tenant deletion closed at — a tenant's deletion fact records when the tenant closed, and is one per tenant.

``tenant_deletions`` gains ``closed_at``: added nullable, filled on every fact from its ``deleted_at`` — or, for a fact
whose deletion is still underway, the time this migration runs — paging along ``id`` in bounded batches, then made
required. ``ix_tenant_deletions_tenant_id`` becomes ``uq_tenant_deletions_tenant_id``: one deletion fact per tenant, the
row a drain locks and the answer to a repeated delete. The upgrade refuses, naming them, while two facts name one
tenant. Each step inspects first and skips what a stopped run already did: the column, its nullability, the constraint.

The downgrade refuses while any deletion is underway, naming the tenants — the older hub has nothing that would finish
it — and otherwise restores the plain index and drops the column.
Revision ID: 20261008_1720_tenant_deletion_closed_at
Revises: 20261008_1710_auth_state_invitation
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime

import sqlalchemy as sa
from alembic import op

from blizzard.foundation.store.migrations import migration_clock
from blizzard.foundation.store.utc import UtcDateTime

revision: str = "20261008_1720_tenant_deletion_closed_at"
down_revision: str | None = "20261008_1710_auth_state_invitation"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_BATCH_ROWS = 1000
_TABLE = "tenant_deletions"
_COLUMN = "closed_at"
_INDEX = "ix_tenant_deletions_tenant_id"
_UNIQUE = "uq_tenant_deletions_tenant_id"

_deletions = sa.table(
    _TABLE,
    sa.column("id", sa.Integer),
    sa.column("tenant_id", sa.String),
    sa.column("deleted_at", UtcDateTime),
    sa.column(_COLUMN, UtcDateTime),
)


def _columns(bind: sa.Connection) -> dict[str, bool]:
    return {c["name"]: bool(c["nullable"]) for c in sa.inspect(bind).get_columns(_TABLE)}


def _index_names(bind: sa.Connection) -> set[str]:
    return {str(i["name"]) for i in sa.inspect(bind).get_indexes(_TABLE)}


def _unique_names(bind: sa.Connection) -> set[str]:
    return {str(u["name"]) for u in sa.inspect(bind).get_unique_constraints(_TABLE)}


def _refuse_a_second_fact(bind: sa.Connection) -> None:
    tenant = _deletions.c.tenant_id
    repeated = bind.execute(sa.select(tenant).group_by(tenant).having(sa.func.count() > 1).order_by(tenant)).scalars()
    named = list(repeated)
    if named:
        raise RuntimeError(
            f"more than one deletion fact names each of {', '.join(named)}; keep one per tenant before upgrading"
        )


def _fill(bind: sa.Connection) -> None:
    """Set ``closed_at`` on every fact where it is null, at most ``_BATCH_ROWS`` per statement, each page seeking
    past the last ``id`` the one before it covered. The time this migration runs is read only when a fact whose
    deletion is underway needs it."""
    unset = _deletions.c[_COLUMN].is_(None)
    page = sa.select(_deletions.c.id).where(unset).order_by(_deletions.c.id).limit(_BATCH_ROWS)
    now: sa.ColumnElement[datetime] | None = None
    after: sa.ColumnElement[bool] = sa.true()
    while ids := bind.execute(page.where(after)).scalars().all():
        last = ids[-1]
        in_page = sa.and_(unset, after, _deletions.c.id <= last)
        if (
            now is None
            and bind.execute(sa.select(sa.exists().where(in_page, _deletions.c.deleted_at.is_(None)))).scalar()
        ):
            now = sa.literal(migration_clock().now(), type_=UtcDateTime())
        value = _deletions.c.deleted_at if now is None else sa.func.coalesce(_deletions.c.deleted_at, now)
        bind.execute(sa.update(_deletions).where(in_page).values({_COLUMN: value}))
        after = _deletions.c.id > last


def upgrade() -> None:
    bind = op.get_bind()
    if _UNIQUE not in _unique_names(bind):
        _refuse_a_second_fact(bind)
    if _COLUMN not in _columns(bind):
        op.add_column(_TABLE, sa.Column(_COLUMN, UtcDateTime(), nullable=True))
    _fill(bind)
    nullable = _columns(bind)[_COLUMN]
    indexed = _INDEX in _index_names(bind)
    unique = _UNIQUE in _unique_names(bind)
    if nullable or indexed or not unique:
        with op.batch_alter_table(_TABLE, recreate="auto") as batch:
            if nullable:
                batch.alter_column(_COLUMN, existing_type=UtcDateTime(), nullable=False)
            if indexed:
                batch.drop_index(_INDEX)
            if not unique:
                batch.create_unique_constraint(_UNIQUE, ["tenant_id"])


def downgrade() -> None:
    bind = op.get_bind()
    if _COLUMN in _columns(bind):
        underway = (
            bind.execute(
                sa.select(_deletions.c.tenant_id)
                .where(_deletions.c.deleted_at.is_(None))
                .order_by(_deletions.c.tenant_id)
            )
            .scalars()
            .all()
        )
        if underway:
            raise RuntimeError(f"the deletion of {', '.join(underway)} is underway; let it complete before downgrading")
    unique = _UNIQUE in _unique_names(bind)
    indexed = _INDEX in _index_names(bind)
    present = _COLUMN in _columns(bind)
    if unique or not indexed or present:
        with op.batch_alter_table(_TABLE, recreate="auto") as batch:
            if unique:
                batch.drop_constraint(_UNIQUE, type_="unique")
            if not indexed:
                batch.create_index(_INDEX, ["tenant_id"])
            if present:
                batch.drop_column(_COLUMN)

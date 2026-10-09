"""tenant identity move — every user's role becomes a membership in the store's only tenant, and ``users.role`` goes.

Each user's ``users.role`` becomes facts beside the account, written ``set_by = 'migration'``: ``guest``,
``contributor`` and ``admin`` a membership of that role in the tenant; ``superuser`` an ``admin`` membership and a
``hub_admin_facts`` grant, so the claimed superuser stays the hub administrator; ``pending`` nothing at all.
``superuser_bootstrap`` and every session are left as they are, so a signed-in person stays signed in and the
configured superuser is never claimed a second time. The copy pages along ``users.id`` in bounded batches, and a user
already holding a ``migration`` fact of a kind — written by a run stopped part way — is not given another. Then
``users.role`` is dropped; a store whose ``users`` has no ``role`` has already carried its roles over.

The downgrade refuses while more than one tenant exists, naming them; with one it re-adds ``users.role``, fills it
along ``users.id`` in bounded batches — ``superuser`` for a user the newest hub-administrator fact grants, else the
role of their membership in force, else ``pending`` — tightens it to ``NOT NULL``, and empties both fact tables, which
the revision before this one created empty.
Revision ID: 20261008_1700_tenant_identity_move
Revises: 20261008_1650_identity_tables
"""

from __future__ import annotations

from collections.abc import Iterator, Sequence

import sqlalchemy as sa
from alembic import op

from blizzard.foundation.store.migrations import migration_clock
from blizzard.foundation.store.utc import UtcDateTime

revision: str = "20261008_1700_tenant_identity_move"
down_revision: str | None = "20261008_1650_identity_tables"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_BATCH_ROWS = 1000
_SET_BY = "migration"
# Restated, not imported, from ``blizzard.auth_core.Role`` (``bzh:frozen-revisions``).
_MEMBER_ROLES = ("guest", "contributor", "admin")
_SUPERUSER = "superuser"
_PENDING = "pending"

_users = sa.table("users", sa.column("id", sa.String), sa.column("role", sa.String))
_tenants = sa.table("tenants", sa.column("tenant_id", sa.String))
_membership_facts = sa.table(
    "membership_facts",
    sa.column("id", sa.Integer),
    sa.column("tenant_id", sa.String),
    sa.column("user_id", sa.String),
    sa.column("role", sa.String),
    sa.column("set_at", UtcDateTime),
    sa.column("set_by", sa.String),
)
_hub_admin_facts = sa.table(
    "hub_admin_facts",
    sa.column("id", sa.Integer),
    sa.column("user_id", sa.String),
    sa.column("granted", sa.Boolean),
    sa.column("set_at", UtcDateTime),
    sa.column("set_by", sa.String),
)


def _tenant_ids(bind: sa.Connection) -> list[str]:
    return list(bind.execute(sa.select(_tenants.c.tenant_id).order_by(_tenants.c.tenant_id)).scalars())


def _has_role_column(bind: sa.Connection) -> bool:
    return "role" in {str(column["name"]) for column in sa.inspect(bind).get_columns("users")}


def _pages(bind: sa.Connection, *columns: sa.ColumnClause[str]) -> Iterator[list[sa.Row[tuple[str, ...]]]]:
    """``users``' rows along ``users.id``, ``_BATCH_ROWS`` to a page — each page read once the caller is done with the
    one before it, by a seek past that page's last id, never a rescan from the start."""
    ids = _users.c.id
    cursor = ""
    while page := list(bind.execute(sa.select(ids, *columns).where(ids > cursor).order_by(ids).limit(_BATCH_ROWS))):
        yield page
        cursor = page[-1].id


def _carried(bind: sa.Connection, table: sa.TableClause, page: list[str]) -> set[str]:
    """The users of ``page`` ``table`` already holds a ``migration`` fact for."""
    c = table.c
    return set(bind.execute(sa.select(c.user_id).where(c.user_id.in_(page), c.set_by == _SET_BY)).scalars())


def _carry_roles_over(bind: sa.Connection, tenant: str) -> None:
    now = migration_clock().now()
    for page in _pages(bind, _users.c.role):
        ids = [row.id for row in page]
        members = _carried(bind, _membership_facts, ids)
        admins = _carried(bind, _hub_admin_facts, ids)
        memberships = [
            {"tenant_id": tenant, "user_id": row.id, "role": "admin" if row.role == _SUPERUSER else row.role}
            for row in page
            if row.role in (*_MEMBER_ROLES, _SUPERUSER) and row.id not in members
        ]
        grants = [{"user_id": row.id} for row in page if row.role == _SUPERUSER and row.id not in admins]
        if memberships:
            bind.execute(sa.insert(_membership_facts).values(set_at=now, set_by=_SET_BY), memberships)
        if grants:
            bind.execute(sa.insert(_hub_admin_facts).values(granted=True, set_at=now, set_by=_SET_BY), grants)


def upgrade() -> None:
    bind = op.get_bind()
    if not _has_role_column(bind):
        return
    tenants = _tenant_ids(bind)
    if len(tenants) != 1:
        raise RuntimeError(f"carrying roles over needs exactly one tenant; the store holds {len(tenants)}")
    _carry_roles_over(bind, tenants[0])
    with op.batch_alter_table("users", recreate="auto") as batch:
        batch.drop_column("role")


def _roles_in_force(bind: sa.Connection, tenant: str | None, page: list[str]) -> dict[str, str]:
    """Each user of ``page``'s role as ``users.role`` held it: ``superuser`` for a hub administrator, else their
    membership's role in ``tenant``; a user holding neither is absent."""
    m, h = _membership_facts.c, _hub_admin_facts.c
    later_m = _membership_facts.alias("later")
    newest_m = ~sa.exists().where(
        later_m.c.tenant_id == m.tenant_id, later_m.c.user_id == m.user_id, later_m.c.id > m.id
    )
    later_h = _hub_admin_facts.alias("later")
    newest_h = ~sa.exists().where(later_h.c.user_id == h.user_id, later_h.c.id > h.id)
    roles: dict[str, str] = dict(
        bind.execute(
            sa.select(m.user_id, m.role).where(
                m.tenant_id == tenant, m.user_id.in_(page), m.role.is_not(None), newest_m
            )
        )
        .tuples()
        .all()
    )
    admins = bind.execute(sa.select(h.user_id).where(h.user_id.in_(page), h.granted.is_(True), newest_h)).scalars()
    roles.update(dict.fromkeys(admins, _SUPERUSER))
    return roles


def downgrade() -> None:
    bind = op.get_bind()
    tenants = _tenant_ids(bind)
    if len(tenants) > 1:
        raise RuntimeError(f"delete every tenant but one before downgrading; the store holds {', '.join(tenants)}")
    tenant = tenants[0] if tenants else None
    if not _has_role_column(bind):
        with op.batch_alter_table("users", recreate="auto") as batch:
            batch.add_column(sa.Column("role", sa.String, nullable=True))
    for page in _pages(bind):
        ids = [row.id for row in page]
        roles = _roles_in_force(bind, tenant, ids)
        holders: dict[str, list[str]] = {}
        for user_id in ids:
            holders.setdefault(roles.get(user_id, _PENDING), []).append(user_id)
        for role, user_ids in holders.items():
            bind.execute(sa.update(_users).where(_users.c.id.in_(user_ids)).values(role=role))
    with op.batch_alter_table("users", recreate="auto") as batch:
        batch.alter_column("role", existing_type=sa.String, nullable=False)
    bind.execute(sa.delete(_hub_admin_facts))
    bind.execute(sa.delete(_membership_facts))

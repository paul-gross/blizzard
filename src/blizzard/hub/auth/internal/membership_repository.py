"""SQLAlchemy adapter for the membership repository seam (package-private).

All ``sqlalchemy`` usage is confined here (``bzh:dependency-inversion``); the domain sees only
:class:`~blizzard.hub.auth.memberships.MembershipFact` and :class:`~blizzard.hub.auth.memberships.Membership`.
``membership_facts`` is global, so this adapter holds hub-level connections."""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy import Select, insert, select

from blizzard.auth_core import Role
from blizzard.hub.auth.memberships import IWriteMembershipRepository, Membership, MembershipFact
from blizzard.hub.store import schema as s
from blizzard.hub.store.errors import HubStoreConnections
from blizzard.hub.tenancy import TenantId

_facts = s.membership_facts


def _in_force(*criteria: sa.ColumnElement[bool]) -> Select[tuple[str, str, str | None]]:
    """The newest fact of each ``(tenant, user)`` matching ``criteria``, revocations included: a fact no later fact of
    its own ``(tenant, user)`` follows."""
    later = _facts.alias("later")
    superseded = sa.exists().where(
        later.c.tenant_id == _facts.c.tenant_id, later.c.user_id == _facts.c.user_id, later.c.id > _facts.c.id
    )
    return select(_facts.c.tenant_id, _facts.c.user_id, _facts.c.role).where(*criteria, ~superseded)


class MembershipRepository:
    """Read-write ``membership_facts`` adapter over the hub store."""

    def __init__(self, store: HubStoreConnections) -> None:
        self._store = store

    # --- reads ----------------------------------------------------------

    def role_in(self, tenant: TenantId, user_id: str) -> Role | None:
        with self._store.read("role_in") as conn:
            role = conn.execute(
                select(_facts.c.role)
                .where(_facts.c.tenant_id == tenant, _facts.c.user_id == user_id)
                .order_by(_facts.c.id.desc())
                .limit(1)
            ).scalar_one_or_none()
            return Role(role) if role is not None else None

    def memberships_for(self, user_id: str) -> list[Membership]:
        with self._store.read("memberships_for") as conn:
            rows = conn.execute(_in_force(_facts.c.user_id == user_id).order_by(_facts.c.tenant_id)).all()
            return [self._membership(row) for row in rows if row.role is not None]

    def members(self, tenant: TenantId) -> list[Membership]:
        with self._store.read("members") as conn:
            rows = conn.execute(_in_force(_facts.c.tenant_id == tenant).order_by(_facts.c.user_id)).all()
            return [self._membership(row) for row in rows if row.role is not None]

    def admin_count(self, tenant: TenantId) -> int:
        with self._store.read("admin_count") as conn:
            admins = _in_force(_facts.c.tenant_id == tenant, _facts.c.role == Role.ADMIN.value).subquery()
            return conn.execute(select(sa.func.count()).select_from(admins)).scalar_one()

    # --- writes -----------------------------------------------------------

    def record(self, fact: MembershipFact) -> None:
        with self._store.write("record") as conn:
            conn.execute(
                insert(_facts).values(
                    tenant_id=fact.tenant_id,
                    user_id=fact.user_id,
                    role=fact.role.value if fact.role is not None else None,
                    set_at=fact.set_at,
                    set_by=fact.set_by,
                )
            )

    # --- helpers ------------------------------------------------------------

    @staticmethod
    def _membership(row) -> Membership:  # type: ignore[no-untyped-def]
        return Membership(tenant_id=TenantId(row.tenant_id), user_id=row.user_id, role=Role(row.role))


def _conforms_membership_repository(x: MembershipRepository) -> IWriteMembershipRepository:
    return x

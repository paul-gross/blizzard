"""Memberships — one user's role in one tenant, recorded as facts (``bzh:facts-not-status``).

The newest fact per ``(tenant, user)`` is in force; a fact with no role revokes. A membership's role is drawn from
:data:`~blizzard.auth_core.MEMBERSHIP_ROLES` alone. The facts are global — a person's memberships are read before any
tenant is known — so the repository holds hub-level connections, never a tenant's. The concrete adapter lives at
``src/blizzard/hub/auth/internal/membership_repository.py`` (``bzh:dependency-inversion``).

:class:`TenantMembers` grants, changes and revokes memberships under the rules a tenant's members are managed by
(:func:`membership_change_refusal`): nobody changes their own role, a user id that is no member answers as one that does
not exist unless the actor may grant anyone (``MEMBERSHIP_GRANT_ANY``), and a tenant keeps its last ``admin``."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, replace
from datetime import datetime
from typing import TYPE_CHECKING, Protocol

from blizzard.auth_core import MEMBERSHIP_ROLES, Permission, Role
from blizzard.foundation.clock import IClock
from blizzard.foundation.roles import domain_model
from blizzard.hub.auth.facts import AuthFactsService
from blizzard.hub.auth.hub_admins import IReadHubAdminRepository
from blizzard.hub.auth.models import ResolvedIdentity, User
from blizzard.hub.auth.users import IReadUserRepository
from blizzard.hub.tenancy import StoreScope, TenantId

if TYPE_CHECKING:
    from blizzard.hub.auth.identity_transaction import IIdentityTransactions

#: ``set_by`` of a membership the identity migration carried over from a user's former role.
SET_BY_MIGRATION = "migration"
#: ``set_by`` of the admin membership the first hub administrator's claim grants.
SET_BY_BOOTSTRAP = "bootstrap"
#: ``set_by`` of a membership granted under ``auth.mode = "none"``, where no user acts.
SET_BY_OPERATOR = "operator"


class MembershipRoleRefused(ValueError):
    """Raised when a membership fact names a role outside :data:`~blizzard.auth_core.MEMBERSHIP_ROLES`."""

    def __init__(self, role: Role) -> None:
        super().__init__(f"{role.value} is not a membership role")
        self.role = role


@domain_model
@dataclass(frozen=True)
class MembershipFact:
    """One grant, change, or revocation (``role`` is ``None``) of ``user_id``'s membership in ``tenant_id``.
    ``set_by`` is the granting user's id, the id of the invitation whose acceptance granted it, or one of the
    ``SET_BY_*`` values."""

    tenant_id: TenantId
    user_id: str
    role: Role | None
    set_at: datetime
    set_by: str

    def __post_init__(self) -> None:
        if self.role is not None and self.role not in MEMBERSHIP_ROLES:
            raise MembershipRoleRefused(self.role)


@domain_model
@dataclass(frozen=True)
class Membership:
    """A membership in force: ``user_id`` holds ``role`` in ``tenant_id``."""

    tenant_id: TenantId
    user_id: str
    role: Role


class IReadMembershipRepository(Protocol):
    """Read-only membership lookups, each answering from the newest fact per ``(tenant, user)``."""

    def role_in(self, tenant: TenantId, user_id: str) -> Role | None:
        """``user_id``'s role in ``tenant``, or ``None`` when they hold no membership there."""
        ...

    def memberships_for(self, user_id: str) -> list[Membership]:
        """Every membership ``user_id`` holds, one per tenant, ordered by tenant id."""
        ...

    def members(self, tenant: TenantId) -> list[Membership]:
        """Every membership in force in ``tenant``, ordered by user id."""
        ...

    def admin_count(self, tenant: TenantId) -> int:
        """How many of ``tenant``'s members hold ``admin``."""
        ...

    def only_tenant(self) -> TenantId | None:
        """The hub's tenant while it holds exactly one, else ``None`` — the one tenant the superuser
        claim may let its claimer into."""
        ...


class IWriteMembershipRepository(IReadMembershipRepository, Protocol):
    """Adds the membership write."""

    def record(self, fact: MembershipFact) -> None:
        """Append ``fact``; it is in force from now on for its ``(tenant, user)``."""
        ...


@domain_model
@dataclass(frozen=True)
class Member:
    """A user as one tenant sees them: their membership's role there (``None`` without one) and whether they are a
    hub administrator."""

    user: User
    tenant: TenantId
    role: Role | None
    hub_admin: bool


class NotAMember(LookupError):
    """Raised when the subject holds no membership in the tenant and the actor may not grant anyone — answered exactly
    as a user id that does not exist, so a membership change never tells who else is on the hub."""

    def __init__(self, user_id: str) -> None:
        super().__init__("no such user")
        self.user_id = user_id


class MembershipChangeRefused(Exception):
    """Raised when the actor changes their own membership."""


class LastAdminKept(Exception):
    """Raised when a change would leave a tenant that has an ``admin`` member without one."""

    def __init__(self, user_id: str, tenant: TenantId) -> None:
        super().__init__(f"{user_id} is the last admin of {tenant}; make another member admin first")
        self.user_id = user_id
        self.tenant = tenant


def membership_change_refusal(
    *, actor: ResolvedIdentity, subject: Member, to_role: Role | None, admins: int
) -> NotAMember | MembershipChangeRefused | LastAdminKept | None:
    """Why ``actor`` may not move ``subject`` to ``to_role`` (``None`` revokes) in a tenant holding ``admins``
    ``admin`` members, or ``None`` when the rules admit it. A non-member is changed only under
    ``MEMBERSHIP_GRANT_ANY``; nobody changes their own role; the last ``admin`` is neither demoted nor revoked."""
    if subject.role is None and Permission.MEMBERSHIP_GRANT_ANY not in actor.permissions:
        return NotAMember(subject.user.user_id)
    if actor.user_id == subject.user.user_id:
        return MembershipChangeRefused("cannot change your own role")
    if subject.role is Role.ADMIN and to_role is not Role.ADMIN and admins <= 1:
        return LastAdminKept(subject.user.user_id, subject.tenant)
    return None


def assignable_roles(*, actor: ResolvedIdentity, subject: Member, admins: int) -> list[Role]:
    """Every membership role the rules let ``actor`` give ``subject`` — empty when they admit no change."""
    return [
        r
        for r in MEMBERSHIP_ROLES
        if membership_change_refusal(actor=actor, subject=subject, to_role=r, admins=admins) is None
    ]


def revocable(*, actor: ResolvedIdentity, subject: Member, admins: int) -> bool:
    """Whether the rules let ``actor`` revoke ``subject``'s membership."""
    return membership_change_refusal(actor=actor, subject=subject, to_role=None, admins=admins) is None


class TenantMembers:
    """Reads a tenant's members and grants, changes and revokes their memberships — each change in one identity unit
    of work, so the rules judge the memberships the change itself reads, and a concurrent change commits either before
    that judgment or after the judged change commits."""

    def __init__(
        self,
        *,
        memberships: IReadMembershipRepository,
        hub_admins: IReadHubAdminRepository,
        users: IReadUserRepository,
        transactions: IIdentityTransactions,
        auth_facts: AuthFactsService,
        clock: IClock,
    ) -> None:
        self._memberships = memberships
        self._hub_admins = hub_admins
        self._users = users
        self._transactions = transactions
        self._auth_facts = auth_facts
        self._clock = clock

    def member(self, user: User, *, tenant: TenantId) -> Member:
        """``user`` as ``tenant`` sees them."""
        return _member(user, tenant, memberships=self._memberships, hub_admins=self._hub_admins)

    def of(self, users: Sequence[User], *, tenant: TenantId) -> list[Member]:
        """Each of ``users`` as ``tenant`` sees them, in order — two reads however many users."""
        roles = {m.user_id: m.role for m in self._memberships.members(tenant)}
        holders = set(self._hub_admins.holders())
        return [Member(u, tenant, roles.get(u.user_id), u.user_id in holders) for u in users]

    def listing(self, tenant: TenantId) -> list[Member]:
        """Every member of ``tenant``, ordered by user id — a fixed number of reads however many members."""
        memberships = self._memberships.members(tenant)
        users = self._users.get_many([m.user_id for m in memberships])
        holders = set(self._hub_admins.holders())
        return [
            Member(users[m.user_id], tenant, m.role, m.user_id in holders) for m in memberships if m.user_id in users
        ]

    def admins(self, tenant: TenantId) -> int:
        """How many of ``tenant``'s members hold ``admin``."""
        return self._memberships.admin_count(tenant)

    def change(self, *, actor: ResolvedIdentity, subject: Member, to_role: Role | None) -> Member:
        """Move ``subject`` to ``to_role`` in its tenant — ``None`` revokes — and record a ``user_role_changed`` fact.

        ``subject`` names the user and tenant; its role and hub administrator standing, and the tenant's admin count,
        are read again inside the unit of work and the rules judge that read. The unit first takes the tenant's lock
        on the condition that it is open, so no close or deletion commits between that read and the change: a tenant
        closed or deleted since ``subject`` was named raises :class:`~blizzard.hub.tenancy.TenantClosed` or
        :class:`~blizzard.hub.tenancy.UnknownTenant` and nothing is written. Raises :class:`NotAMember`,
        :class:`MembershipChangeRefused` or :class:`LastAdminKept`. A change to the role already in force writes
        nothing and returns the subject as read."""
        with self._transactions.begin() as writes:
            with writes.tenant_connections(StoreScope(subject.tenant)).write("lock_open_tenant"):
                pass
            current = _member(
                subject.user, subject.tenant, memberships=writes.memberships, hub_admins=writes.hub_admins
            )
            refusal = membership_change_refusal(
                actor=actor, subject=current, to_role=to_role, admins=writes.memberships.admin_count(current.tenant)
            )
            if refusal is not None:
                raise refusal
            if current.role is to_role:
                return current
            writes.memberships.record(
                MembershipFact(current.tenant, current.user.user_id, to_role, self._clock.now(), actor.user_id)
            )
            self._auth_facts.user_role_changed(
                actor=actor.username,
                subject=current.user.username,
                tenant=current.tenant,
                from_role=current.role,
                to_role=to_role,
                into=writes.auth_facts,
            )
        return replace(current, role=to_role)


def _member(
    user: User, tenant: TenantId, *, memberships: IReadMembershipRepository, hub_admins: IReadHubAdminRepository
) -> Member:
    return Member(
        user=user,
        tenant=tenant,
        role=memberships.role_in(tenant, user.user_id),
        hub_admin=hub_admins.is_hub_admin(user.user_id),
    )

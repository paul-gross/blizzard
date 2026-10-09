"""Memberships — one user's role in one tenant, recorded as facts (``bzh:facts-not-status``).

The newest fact per ``(tenant, user)`` is in force; a fact with no role revokes. A membership's role is drawn from
:data:`~blizzard.auth_core.MEMBERSHIP_ROLES` alone. The facts are global — a person's memberships are read before any
tenant is known — so the repository holds hub-level connections, never a tenant's. The concrete adapter lives at
``src/blizzard/hub/auth/internal/membership_repository.py`` (``bzh:dependency-inversion``)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from blizzard.auth_core import MEMBERSHIP_ROLES, Role
from blizzard.foundation.roles import domain_model
from blizzard.hub.tenancy import TenantId

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
    ``set_by`` is the granting user's id, or one of the ``SET_BY_*`` values."""

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


class IWriteMembershipRepository(IReadMembershipRepository, Protocol):
    """Adds the membership write."""

    def record(self, fact: MembershipFact) -> None:
        """Append ``fact``; it is in force from now on for its ``(tenant, user)``."""
        ...

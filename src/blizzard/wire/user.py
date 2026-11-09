"""People wire bodies — member, hub-administrator, and user views, the role requests, and the
caller's tenant views."""

from __future__ import annotations

from typing import Annotated, Final, Literal

from pydantic import BaseModel

from blizzard.auth_core import Role
from blizzard.foundation.unrecognized import Unrecognized
from blizzard.wire.base import WireModel
from blizzard.wire.problem import ProblemDetails
from blizzard.wire.vocabulary import OPEN


class UserIdentityView(BaseModel):
    """One linked provider identity."""

    provider_name: str
    handle: str


class MemberView(WireModel):
    """One member of the request's tenant."""

    user_id: str
    username: str
    display_name: str
    email: str | None
    #: The membership's role in the tenant — ``guest``, ``contributor`` or ``admin``.
    role: Annotated[Role | Unrecognized, OPEN]
    #: Whether the member also holds the hub administrator role, which grants nothing inside the tenant by itself.
    hub_admin: bool
    created_at: str
    identities: list[UserIdentityView] = []
    #: The membership roles the requesting actor may give this member — empty when the rules admit no change.
    assignable_roles: list[Annotated[Role | Unrecognized, OPEN]] = []
    #: Whether the requesting actor may revoke this membership.
    revocable: bool = False


class MemberRoleRequest(BaseModel):
    """``PUT /api/members/{user_id}`` body — the membership role to grant: ``guest``, ``contributor`` or ``admin``."""

    role: Role


class HubAdminView(BaseModel):
    """One hub administrator."""

    user_id: str
    username: str
    display_name: str
    email: str | None


class UserView(WireModel):
    """One member as the deprecated ``/api/users`` routes render them — ``role`` is ``superuser`` for a hub
    administrator, else the membership's role, ``pending`` once revoked."""

    user_id: str
    username: str
    display_name: str
    email: str | None
    role: Annotated[Role | Unrecognized, OPEN]
    created_at: str
    identities: list[UserIdentityView] = []
    #: The roles the requesting actor may assign this user — ``pending`` revokes; empty when the rules admit none.
    assignable_roles: list[Annotated[Role | Unrecognized, OPEN]] = []


class RoleAssignmentRequest(BaseModel):
    """``POST /api/users/{id}/role`` body — the target role, by its wire value; ``pending`` revokes."""

    role: str


class CallerTenantView(BaseModel):
    """One tenant ``GET /api/me`` says the caller may enter: its id, the only way to name it, and its name."""

    tenant_id: str
    name: str


class CallerTenantsView(BaseModel):
    """``GET /api/me`` as a client that only picks a tenant reads it — the tenants the caller may enter, ordered by
    id, and nothing else of the answer. Empty from a hub that predates tenants."""

    tenants: list[CallerTenantView] = []


USER_NOT_FOUND: Final = "urn:blizzard:problem:user-not-found"


MEMBER_NOT_FOUND: Final = "urn:blizzard:problem:member-not-found"


LAST_HUB_ADMIN: Final = "urn:blizzard:problem:last-hub-admin"


LAST_ADMIN_KEPT: Final = "urn:blizzard:problem:last-admin-kept"


MEMBERSHIP_CHANGE_REFUSED: Final = "urn:blizzard:problem:membership-change-refused"


ROLE_NOT_ASSIGNABLE: Final = "urn:blizzard:problem:role-not-assignable"


UNKNOWN_ROLE: Final = "urn:blizzard:problem:unknown-role"


class UserNotFoundProblem(ProblemDetails[Literal["urn:blizzard:problem:user-not-found"]]):
    """The named user does not exist."""


class MemberNotFoundProblem(ProblemDetails[Literal["urn:blizzard:problem:member-not-found"]]):
    """The named user is not a member of the tenant."""


class LastHubAdminProblem(ProblemDetails[Literal["urn:blizzard:problem:last-hub-admin"]]):
    """The last hub administrator cannot be revoked."""


class LastAdminKeptProblem(ProblemDetails[Literal["urn:blizzard:problem:last-admin-kept"]]):
    """The tenant's last admin cannot be demoted or revoked."""


class MembershipChangeRefusedProblem(ProblemDetails[Literal["urn:blizzard:problem:membership-change-refused"]]):
    """A caller may not change their own membership role."""


class RoleNotAssignableProblem(ProblemDetails[Literal["urn:blizzard:problem:role-not-assignable"]]):
    """The role is not assignable through the API."""


class UnknownRoleProblem(ProblemDetails[Literal["urn:blizzard:problem:unknown-role"]]):
    """The named role is not a role."""


USER_PROBLEM_TYPES: frozenset[str] = frozenset(
    {
        USER_NOT_FOUND,
        MEMBER_NOT_FOUND,
        LAST_HUB_ADMIN,
        LAST_ADMIN_KEPT,
        MEMBERSHIP_CHANGE_REFUSED,
        ROLE_NOT_ASSIGNABLE,
        UNKNOWN_ROLE,
    }
)

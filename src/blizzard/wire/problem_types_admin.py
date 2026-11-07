"""Wire problems for the admin family's refusals: configuration records, secrets, members, invitations, tenants,
hub administrators, users and graphs.

Each type names one meaning; a status and ``detail`` are the ones the refusal answered before it was typed."""

from __future__ import annotations

from typing import Final, Literal

from pydantic import BaseModel

from blizzard.wire.base import WireModel
from blizzard.wire.problem import CommonProblemMembers, ProblemDetails, ValidationEntry

PROJECT_NOT_FOUND: Final = "urn:blizzard:problem:project-not-found"
PROJECT_LINK_NOT_FOUND: Final = "urn:blizzard:problem:project-link-not-found"
FORGE_NOT_FOUND: Final = "urn:blizzard:problem:forge-not-found"
REPOSITORY_NOT_FOUND: Final = "urn:blizzard:problem:repository-not-found"
WORK_SOURCE_NOT_FOUND: Final = "urn:blizzard:problem:work-source-not-found"
GRAPH_NOT_FOUND: Final = "urn:blizzard:problem:graph-not-found"
CONFIG_SCHEMA_NOT_FOUND: Final = "urn:blizzard:problem:config-schema-not-found"
USER_NOT_FOUND: Final = "urn:blizzard:problem:user-not-found"
MEMBER_NOT_FOUND: Final = "urn:blizzard:problem:member-not-found"
INVITATION_NOT_FOUND: Final = "urn:blizzard:problem:invitation-not-found"
WORK_ITEM_NOT_FOUND: Final = "urn:blizzard:problem:work-item-not-found"
PROJECT_SLUG_TAKEN: Final = "urn:blizzard:problem:project-slug-taken"
REPOSITORY_SLUG_TAKEN: Final = "urn:blizzard:problem:repository-slug-taken"
REPOSITORY_COORDINATE_TAKEN: Final = "urn:blizzard:problem:repository-coordinate-taken"
WORK_SOURCE_SLUG_TAKEN: Final = "urn:blizzard:problem:work-source-slug-taken"
WORK_SOURCE_LOCATOR_TAKEN: Final = "urn:blizzard:problem:work-source-locator-taken"
WORK_SOURCE_NO_EDITOR: Final = "urn:blizzard:problem:work-source-no-editor"
WORK_ITEM_NOT_EDITABLE: Final = "urn:blizzard:problem:work-item-not-editable"
WORK_ITEM_HELD_BY_LIVE_CHUNK: Final = "urn:blizzard:problem:work-item-held-by-live-chunk"
WORK_ITEM_HELD_BY_DEPENDENTS: Final = "urn:blizzard:problem:work-item-held-by-dependents"
WORK_ITEM_FIELD_BLANK: Final = "urn:blizzard:problem:work-item-field-blank"
DEFAULT_GRAPH_RETIRED: Final = "urn:blizzard:problem:default-graph-retired"
SECRET_RETIRED: Final = "urn:blizzard:problem:secret-retired"
SECRET_NAME_INVALID: Final = "urn:blizzard:problem:secret-name-invalid"
SECRET_VALUE_BLANK: Final = "urn:blizzard:problem:secret-value-blank"
INVITATION_ALREADY_RESOLVED: Final = "urn:blizzard:problem:invitation-already-resolved"
LAST_HUB_ADMIN: Final = "urn:blizzard:problem:last-hub-admin"
LAST_ADMIN_KEPT: Final = "urn:blizzard:problem:last-admin-kept"
MEMBERSHIP_CHANGE_REFUSED: Final = "urn:blizzard:problem:membership-change-refused"
ROLE_NOT_ASSIGNABLE: Final = "urn:blizzard:problem:role-not-assignable"
UNKNOWN_ROLE: Final = "urn:blizzard:problem:unknown-role"
TENANT_NAME_REFUSED: Final = "urn:blizzard:problem:tenant-name-refused"
TENANT_DELETED: Final = "urn:blizzard:problem:tenant-deleted"
LANDING_ORDER_CONFLICT: Final = "urn:blizzard:problem:landing-order-conflict"
LAST_TENANT: Final = "urn:blizzard:problem:last-tenant"
FORGE_IN_USE: Final = "urn:blizzard:problem:forge-in-use"
FORGE_REVISION_CONFLICT: Final = "urn:blizzard:problem:forge-revision-conflict"
FORGE_ORIGIN_TAKEN: Final = "urn:blizzard:problem:forge-origin-taken"
FORGE_SLUG_TAKEN: Final = "urn:blizzard:problem:forge-slug-taken"
SECRET_IN_USE: Final = "urn:blizzard:problem:secret-in-use"
CONFIG_FIELD_INVALID: Final = "urn:blizzard:problem:config-field-invalid"
UNKNOWN_WORK_SOURCE: Final = "urn:blizzard:problem:unknown-work-source"

ADMIN_PROBLEM_TYPES: frozenset[str] = frozenset(
    {
        PROJECT_NOT_FOUND,
        PROJECT_LINK_NOT_FOUND,
        FORGE_NOT_FOUND,
        REPOSITORY_NOT_FOUND,
        WORK_SOURCE_NOT_FOUND,
        GRAPH_NOT_FOUND,
        CONFIG_SCHEMA_NOT_FOUND,
        USER_NOT_FOUND,
        MEMBER_NOT_FOUND,
        INVITATION_NOT_FOUND,
        WORK_ITEM_NOT_FOUND,
        PROJECT_SLUG_TAKEN,
        REPOSITORY_SLUG_TAKEN,
        REPOSITORY_COORDINATE_TAKEN,
        WORK_SOURCE_SLUG_TAKEN,
        WORK_SOURCE_LOCATOR_TAKEN,
        WORK_SOURCE_NO_EDITOR,
        WORK_ITEM_NOT_EDITABLE,
        WORK_ITEM_HELD_BY_LIVE_CHUNK,
        WORK_ITEM_HELD_BY_DEPENDENTS,
        WORK_ITEM_FIELD_BLANK,
        DEFAULT_GRAPH_RETIRED,
        SECRET_RETIRED,
        SECRET_NAME_INVALID,
        SECRET_VALUE_BLANK,
        INVITATION_ALREADY_RESOLVED,
        LAST_HUB_ADMIN,
        LAST_ADMIN_KEPT,
        MEMBERSHIP_CHANGE_REFUSED,
        ROLE_NOT_ASSIGNABLE,
        UNKNOWN_ROLE,
        TENANT_NAME_REFUSED,
        TENANT_DELETED,
        LANDING_ORDER_CONFLICT,
        LAST_TENANT,
        FORGE_IN_USE,
        FORGE_REVISION_CONFLICT,
        FORGE_ORIGIN_TAKEN,
        FORGE_SLUG_TAKEN,
        SECRET_IN_USE,
        CONFIG_FIELD_INVALID,
        UNKNOWN_WORK_SOURCE,
    }
)


class ProjectNotFoundProblem(ProblemDetails[Literal["urn:blizzard:problem:project-not-found"]]):
    """A path names a project the tenant does not hold."""


class ProjectLinkNotFoundProblem(ProblemDetails[Literal["urn:blizzard:problem:project-link-not-found"]]):
    """The project holds no link to the named work source or repository."""


class ForgeNotFoundProblem(ProblemDetails[Literal["urn:blizzard:problem:forge-not-found"]]):
    """A path names a forge the tenant does not hold."""


class RepositoryNotFoundProblem(ProblemDetails[Literal["urn:blizzard:problem:repository-not-found"]]):
    """A path names a repository the tenant does not hold."""


class WorkSourceNotFoundProblem(ProblemDetails[Literal["urn:blizzard:problem:work-source-not-found"]]):
    """A path or request names a work source the tenant does not hold."""


class GraphNotFoundProblem(ProblemDetails[Literal["urn:blizzard:problem:graph-not-found"]]):
    """The named graph does not exist in the tenant."""


class ConfigSchemaNotFoundProblem(ProblemDetails[Literal["urn:blizzard:problem:config-schema-not-found"]]):
    """No document schema exists for the named record kind."""


class UserNotFoundProblem(ProblemDetails[Literal["urn:blizzard:problem:user-not-found"]]):
    """The named user does not exist."""


class MemberNotFoundProblem(ProblemDetails[Literal["urn:blizzard:problem:member-not-found"]]):
    """The named user is not a member of the tenant."""


class InvitationNotFoundProblem(ProblemDetails[Literal["urn:blizzard:problem:invitation-not-found"]]):
    """The tenant holds no such invitation."""


class WorkItemNotFoundProblem(ProblemDetails[Literal["urn:blizzard:problem:work-item-not-found"]]):
    """The work source holds no item at the named ref."""


class ProjectSlugTakenProblem(ProblemDetails[Literal["urn:blizzard:problem:project-slug-taken"]]):
    """Another project of the tenant holds the slug."""


class RepositorySlugTakenProblem(ProblemDetails[Literal["urn:blizzard:problem:repository-slug-taken"]]):
    """Another repository of the tenant holds the slug."""


class RepositoryCoordinateTakenProblem(ProblemDetails[Literal["urn:blizzard:problem:repository-coordinate-taken"]]):
    """Another repository holds the forge, owner and repo."""


class WorkSourceSlugTakenProblem(ProblemDetails[Literal["urn:blizzard:problem:work-source-slug-taken"]]):
    """Another work source of the tenant holds the slug."""


class WorkSourceLocatorTakenProblem(ProblemDetails[Literal["urn:blizzard:problem:work-source-locator-taken"]]):
    """Another work source holds the provider and locator."""


class WorkSourceNoEditorProblem(ProblemDetails[Literal["urn:blizzard:problem:work-source-no-editor"]]):
    """The work source offers no editing of its items."""


class WorkItemNotEditableProblem(ProblemDetails[Literal["urn:blizzard:problem:work-item-not-editable"]]):
    """The work item already carries a closure."""


class WorkItemHeldByLiveChunkProblem(ProblemDetails[Literal["urn:blizzard:problem:work-item-held-by-live-chunk"]]):
    """A live chunk still holds the work item."""


class WorkItemHeldByDependentsProblem(ProblemDetails[Literal["urn:blizzard:problem:work-item-held-by-dependents"]]):
    """The work item's holder is a standing prerequisite of other chunks."""


class WorkItemFieldBlankProblem(ProblemDetails[Literal["urn:blizzard:problem:work-item-field-blank"]]):
    """A work item's title or body is blank."""


class DefaultGraphRetiredProblem(ProblemDetails[Literal["urn:blizzard:problem:default-graph-retired"]]):
    """Every graph named after the packaged default is retired."""


class SecretRetiredProblem(ProblemDetails[Literal["urn:blizzard:problem:secret-retired"]]):
    """The secret is retired and must be enabled first."""


class SecretNameInvalidProblem(ProblemDetails[Literal["urn:blizzard:problem:secret-name-invalid"]]):
    """The secret name is not a valid name."""


class SecretValueBlankProblem(ProblemDetails[Literal["urn:blizzard:problem:secret-value-blank"]]):
    """The secret value is blank."""


class InvitationAlreadyResolvedProblem(ProblemDetails[Literal["urn:blizzard:problem:invitation-already-resolved"]]):
    """The invitation was already accepted or revoked."""


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


class TenantNameRefusedProblem(ProblemDetails[Literal["urn:blizzard:problem:tenant-name-refused"]]):
    """The name is not a tenant name's shape."""


class TenantDeletedProblem(ProblemDetails[Literal["urn:blizzard:problem:tenant-deleted"]]):
    """The tenant was already deleted."""


class LandingOrderConflictDetail(WireModel):
    code: Literal["landing_order_conflict"] = "landing_order_conflict"
    message: str


class LastTenantDetail(WireModel):
    code: Literal["last_tenant"] = "last_tenant"
    message: str


class RecordReferrer(WireModel):
    """An enabled record that still names the forge or secret, by kind and key. ``label`` is the record's current slug
    for a slugged kind, whose key is its id; ``None`` for a kind keyed by its name."""

    kind: str
    key: str
    label: str | None = None


class ForgeInUseDetail(WireModel):
    code: Literal["forge_in_use"] = "forge_in_use"
    message: str
    referrers: list[RecordReferrer]


class ForgeRevisionConflictDetail(WireModel):
    code: Literal["revision_conflict"] = "revision_conflict"
    message: str
    current: int


class ForgeOriginTakenDetail(WireModel):
    code: Literal["forge_origin_taken"] = "forge_origin_taken"
    message: str
    holder: str
    api_base: str


class ForgeSlugTakenDetail(WireModel):
    code: Literal["forge_slug_taken"] = "forge_slug_taken"
    message: str
    slug: str


class SecretInUseDetail(WireModel):
    code: Literal["secret_in_use"] = "secret_in_use"
    message: str
    referrers: list[RecordReferrer]


class UnknownWorkSourceDetail(BaseModel):
    """The validation-shaped entry for an unknown work source."""

    loc: list[str]
    msg: str
    type: Literal["unknown_work_source"]


class LandingOrderConflictProblem(
    CommonProblemMembers[Literal["urn:blizzard:problem:landing-order-conflict"], LandingOrderConflictDetail]
):
    """Concurrent landing-order writes kept changing the order."""

    status: int | None = None


class LastTenantProblem(CommonProblemMembers[Literal["urn:blizzard:problem:last-tenant"], LastTenantDetail]):
    """The hub's only open tenant cannot be deleted."""

    status: int | None = None


class ForgeInUseProblem(CommonProblemMembers[Literal["urn:blizzard:problem:forge-in-use"], ForgeInUseDetail]):
    """Enabled records still name the forge."""

    status: int | None = None


class ForgeRevisionConflictProblem(
    CommonProblemMembers[Literal["urn:blizzard:problem:forge-revision-conflict"], ForgeRevisionConflictDetail]
):
    """A forge write read a revision the store no longer holds."""

    status: int | None = None


class ForgeOriginTakenProblem(
    CommonProblemMembers[Literal["urn:blizzard:problem:forge-origin-taken"], ForgeOriginTakenDetail]
):
    """Another forge holds the API origin."""

    status: int | None = None


class ForgeSlugTakenProblem(
    CommonProblemMembers[Literal["urn:blizzard:problem:forge-slug-taken"], ForgeSlugTakenDetail]
):
    """Another forge holds the slug."""

    status: int | None = None


class SecretInUseProblem(CommonProblemMembers[Literal["urn:blizzard:problem:secret-in-use"], SecretInUseDetail]):
    """Active records still reference the secret."""

    status: int | None = None


class ConfigFieldInvalidProblem(
    CommonProblemMembers[Literal["urn:blizzard:problem:config-field-invalid"], list[ValidationEntry]]
):
    """A configured record's field cannot mean anything; ``detail`` locates it in the body."""

    status: int | None = None


class UnknownWorkSourceProblem(
    CommonProblemMembers[Literal["urn:blizzard:problem:unknown-work-source"], list[UnknownWorkSourceDetail]]
):
    """A query names a work source the tenant does not hold."""

    status: int | None = None

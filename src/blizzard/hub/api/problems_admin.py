"""Factories for the admin family's problems. Callers supply the already-sanitized instance and detail."""

from __future__ import annotations

from blizzard.wire import config as config_wire
from blizzard.wire import credential as credential_wire
from blizzard.wire import forge as forge_wire
from blizzard.wire import graph as graph_wire
from blizzard.wire import invitation as invitation_wire
from blizzard.wire import project as project_wire
from blizzard.wire import record_referrer as record_referrer_wire
from blizzard.wire import repository as repository_wire
from blizzard.wire import secret as secret_wire
from blizzard.wire import tenant as tenant_wire
from blizzard.wire import urgency as urgency_wire
from blizzard.wire import user as user_wire
from blizzard.wire import work_source as work_source_wire
from blizzard.wire.problem import ValidationEntry
from blizzard.wire.project import UNKNOWN_PROJECT, UnknownProjectDetail, UnknownProjectProblem


def project_not_found_problem(detail: str, *, instance: str) -> project_wire.ProjectNotFoundProblem:
    return project_wire.ProjectNotFoundProblem(
        type=project_wire.PROJECT_NOT_FOUND, title="Project not found", detail=detail, instance=instance, status=404
    )


def project_link_not_found_problem(detail: str, *, instance: str) -> project_wire.ProjectLinkNotFoundProblem:
    return project_wire.ProjectLinkNotFoundProblem(
        type=project_wire.PROJECT_LINK_NOT_FOUND,
        title="Project link not found",
        detail=detail,
        instance=instance,
        status=404,
    )


def forge_not_found_problem(detail: str, *, instance: str) -> forge_wire.ForgeNotFoundProblem:
    return forge_wire.ForgeNotFoundProblem(
        type=forge_wire.FORGE_NOT_FOUND, title="Forge not found", detail=detail, instance=instance, status=404
    )


def urgency_not_found_problem(detail: str, *, instance: str) -> urgency_wire.UrgencyNotFoundProblem:
    return urgency_wire.UrgencyNotFoundProblem(
        type=urgency_wire.URGENCY_NOT_FOUND, title="Urgency not found", detail=detail, instance=instance, status=404
    )


def urgency_icon_not_found_problem(detail: str, *, instance: str) -> urgency_wire.UrgencyIconNotFoundProblem:
    return urgency_wire.UrgencyIconNotFoundProblem(
        type=urgency_wire.URGENCY_ICON_NOT_FOUND,
        title="Urgency icon not found",
        detail=detail,
        instance=instance,
        status=404,
    )


def urgency_is_default_problem(message: str, slug: str, *, instance: str) -> urgency_wire.UrgencyIsDefaultProblem:
    return urgency_wire.UrgencyIsDefaultProblem(
        type=urgency_wire.URGENCY_IS_DEFAULT,
        title="Urgency is the default",
        detail=message,
        slug=slug,
        instance=instance,
        status=409,
    )


def urgency_in_use_problem(
    message: str, slug: str, open_chunks: int, *, instance: str
) -> urgency_wire.UrgencyInUseProblem:
    return urgency_wire.UrgencyInUseProblem(
        type=urgency_wire.URGENCY_IN_USE,
        title="Urgency in use",
        detail=message,
        slug=slug,
        open_chunks=open_chunks,
        instance=instance,
        status=409,
    )


def repository_not_found_problem(detail: str, *, instance: str) -> repository_wire.RepositoryNotFoundProblem:
    return repository_wire.RepositoryNotFoundProblem(
        type=repository_wire.REPOSITORY_NOT_FOUND,
        title="Repository not found",
        detail=detail,
        instance=instance,
        status=404,
    )


def work_source_not_found_problem(detail: str, *, instance: str) -> work_source_wire.WorkSourceNotFoundProblem:
    return work_source_wire.WorkSourceNotFoundProblem(
        type=work_source_wire.WORK_SOURCE_NOT_FOUND,
        title="Work source not found",
        detail=detail,
        instance=instance,
        status=404,
    )


def graph_not_found_problem(detail: str, *, instance: str) -> graph_wire.GraphNotFoundProblem:
    return graph_wire.GraphNotFoundProblem(
        type=graph_wire.GRAPH_NOT_FOUND, title="Graph not found", detail=detail, instance=instance, status=404
    )


def config_schema_not_found_problem(detail: str, *, instance: str) -> config_wire.ConfigSchemaNotFoundProblem:
    return config_wire.ConfigSchemaNotFoundProblem(
        type=config_wire.CONFIG_SCHEMA_NOT_FOUND,
        title="Config schema not found",
        detail=detail,
        instance=instance,
        status=404,
    )


def user_not_found_problem(detail: str, *, instance: str) -> user_wire.UserNotFoundProblem:
    return user_wire.UserNotFoundProblem(
        type=user_wire.USER_NOT_FOUND, title="User not found", detail=detail, instance=instance, status=404
    )


def member_not_found_problem(detail: str, *, instance: str) -> user_wire.MemberNotFoundProblem:
    return user_wire.MemberNotFoundProblem(
        type=user_wire.MEMBER_NOT_FOUND, title="Member not found", detail=detail, instance=instance, status=404
    )


def invitation_not_found_problem(detail: str, *, instance: str) -> invitation_wire.InvitationNotFoundProblem:
    return invitation_wire.InvitationNotFoundProblem(
        type=invitation_wire.INVITATION_NOT_FOUND,
        title="Invitation not found",
        detail=detail,
        instance=instance,
        status=404,
    )


def work_item_not_found_problem(detail: str, *, instance: str) -> work_source_wire.WorkItemNotFoundProblem:
    return work_source_wire.WorkItemNotFoundProblem(
        type=work_source_wire.WORK_ITEM_NOT_FOUND,
        title="Work item not found",
        detail=detail,
        instance=instance,
        status=404,
    )


def project_slug_taken_problem(detail: str, *, instance: str) -> project_wire.ProjectSlugTakenProblem:
    return project_wire.ProjectSlugTakenProblem(
        type=project_wire.PROJECT_SLUG_TAKEN, title="Project slug taken", detail=detail, instance=instance, status=409
    )


def repository_slug_taken_problem(detail: str, *, instance: str) -> repository_wire.RepositorySlugTakenProblem:
    return repository_wire.RepositorySlugTakenProblem(
        type=repository_wire.REPOSITORY_SLUG_TAKEN,
        title="Repository slug taken",
        detail=detail,
        instance=instance,
        status=409,
    )


def repository_coordinate_taken_problem(
    detail: str, *, instance: str
) -> repository_wire.RepositoryCoordinateTakenProblem:
    return repository_wire.RepositoryCoordinateTakenProblem(
        type=repository_wire.REPOSITORY_COORDINATE_TAKEN,
        title="Repository coordinate taken",
        detail=detail,
        instance=instance,
        status=409,
    )


def work_source_slug_taken_problem(detail: str, *, instance: str) -> work_source_wire.WorkSourceSlugTakenProblem:
    return work_source_wire.WorkSourceSlugTakenProblem(
        type=work_source_wire.WORK_SOURCE_SLUG_TAKEN,
        title="Work source slug taken",
        detail=detail,
        instance=instance,
        status=409,
    )


def work_source_locator_taken_problem(detail: str, *, instance: str) -> work_source_wire.WorkSourceLocatorTakenProblem:
    return work_source_wire.WorkSourceLocatorTakenProblem(
        type=work_source_wire.WORK_SOURCE_LOCATOR_TAKEN,
        title="Work source locator taken",
        detail=detail,
        instance=instance,
        status=409,
    )


def work_source_no_editor_problem(detail: str, *, instance: str) -> work_source_wire.WorkSourceNoEditorProblem:
    return work_source_wire.WorkSourceNoEditorProblem(
        type=work_source_wire.WORK_SOURCE_NO_EDITOR,
        title="Work source no editor",
        detail=detail,
        instance=instance,
        status=409,
    )


def work_item_not_editable_problem(detail: str, *, instance: str) -> work_source_wire.WorkItemNotEditableProblem:
    return work_source_wire.WorkItemNotEditableProblem(
        type=work_source_wire.WORK_ITEM_NOT_EDITABLE,
        title="Work item not editable",
        detail=detail,
        instance=instance,
        status=409,
    )


def work_item_held_by_live_chunk_problem(
    detail: str, *, instance: str
) -> work_source_wire.WorkItemHeldByLiveChunkProblem:
    return work_source_wire.WorkItemHeldByLiveChunkProblem(
        type=work_source_wire.WORK_ITEM_HELD_BY_LIVE_CHUNK,
        title="Work item held by live chunk",
        detail=detail,
        instance=instance,
        status=409,
    )


def work_item_held_by_dependents_problem(
    detail: str, *, instance: str
) -> work_source_wire.WorkItemHeldByDependentsProblem:
    return work_source_wire.WorkItemHeldByDependentsProblem(
        type=work_source_wire.WORK_ITEM_HELD_BY_DEPENDENTS,
        title="Work item held by dependents",
        detail=detail,
        instance=instance,
        status=409,
    )


def work_item_field_blank_problem(detail: str, *, instance: str) -> work_source_wire.WorkItemFieldBlankProblem:
    return work_source_wire.WorkItemFieldBlankProblem(
        type=work_source_wire.WORK_ITEM_FIELD_BLANK,
        title="Work item field blank",
        detail=detail,
        instance=instance,
        status=422,
    )


def default_graph_retired_problem(detail: str, *, instance: str) -> graph_wire.DefaultGraphRetiredProblem:
    return graph_wire.DefaultGraphRetiredProblem(
        type=graph_wire.DEFAULT_GRAPH_RETIRED,
        title="Default graph retired",
        detail=detail,
        instance=instance,
        status=503,
    )


def secret_retired_problem(detail: str, *, instance: str) -> secret_wire.SecretRetiredProblem:
    return secret_wire.SecretRetiredProblem(
        type=secret_wire.SECRET_RETIRED, title="Secret retired", detail=detail, instance=instance, status=409
    )


def secret_name_invalid_problem(detail: str, *, instance: str) -> secret_wire.SecretNameInvalidProblem:
    return secret_wire.SecretNameInvalidProblem(
        type=secret_wire.SECRET_NAME_INVALID, title="Secret name invalid", detail=detail, instance=instance, status=422
    )


def secret_value_blank_problem(detail: str, *, instance: str) -> secret_wire.SecretValueBlankProblem:
    return secret_wire.SecretValueBlankProblem(
        type=secret_wire.SECRET_VALUE_BLANK, title="Secret value blank", detail=detail, instance=instance, status=422
    )


def invitation_already_resolved_problem(
    detail: str, *, instance: str
) -> invitation_wire.InvitationAlreadyResolvedProblem:
    return invitation_wire.InvitationAlreadyResolvedProblem(
        type=invitation_wire.INVITATION_ALREADY_RESOLVED,
        title="Invitation already resolved",
        detail=detail,
        instance=instance,
        status=409,
    )


def last_hub_admin_problem(detail: str, *, instance: str) -> user_wire.LastHubAdminProblem:
    return user_wire.LastHubAdminProblem(
        type=user_wire.LAST_HUB_ADMIN, title="Last hub admin", detail=detail, instance=instance, status=409
    )


def last_admin_kept_problem(detail: str, *, instance: str) -> user_wire.LastAdminKeptProblem:
    return user_wire.LastAdminKeptProblem(
        type=user_wire.LAST_ADMIN_KEPT, title="Last admin kept", detail=detail, instance=instance, status=409
    )


def membership_change_refused_problem(detail: str, *, instance: str) -> user_wire.MembershipChangeRefusedProblem:
    return user_wire.MembershipChangeRefusedProblem(
        type=user_wire.MEMBERSHIP_CHANGE_REFUSED,
        title="Membership change refused",
        detail=detail,
        instance=instance,
        status=403,
    )


def role_not_assignable_problem(detail: str, *, instance: str) -> user_wire.RoleNotAssignableProblem:
    return user_wire.RoleNotAssignableProblem(
        type=user_wire.ROLE_NOT_ASSIGNABLE, title="Role not assignable", detail=detail, instance=instance, status=403
    )


def unknown_role_problem(detail: str, *, instance: str) -> user_wire.UnknownRoleProblem:
    return user_wire.UnknownRoleProblem(
        type=user_wire.UNKNOWN_ROLE, title="Unknown role", detail=detail, instance=instance, status=400
    )


def tenant_name_refused_problem(detail: str, *, instance: str) -> tenant_wire.TenantNameRefusedProblem:
    return tenant_wire.TenantNameRefusedProblem(
        type=tenant_wire.TENANT_NAME_REFUSED, title="Tenant name refused", detail=detail, instance=instance, status=422
    )


def tenant_deleted_problem(detail: str, *, instance: str) -> tenant_wire.TenantDeletedProblem:
    return tenant_wire.TenantDeletedProblem(
        type=tenant_wire.TENANT_DELETED, title="Tenant deleted", detail=detail, instance=instance, status=410
    )


def landing_order_conflict_problem(message: str, *, instance: str) -> project_wire.LandingOrderConflictProblem:
    return project_wire.LandingOrderConflictProblem(
        type=project_wire.LANDING_ORDER_CONFLICT,
        title="Landing order conflict",
        detail=project_wire.LandingOrderConflictDetail(message=message),
        instance=instance,
        status=409,
    )


def last_tenant_problem(*, instance: str) -> tenant_wire.LastTenantProblem:
    return tenant_wire.LastTenantProblem(
        type=tenant_wire.LAST_TENANT,
        title="Last tenant",
        detail=tenant_wire.LastTenantDetail(message="the hub's only open tenant cannot be deleted"),
        instance=instance,
        status=409,
    )


def forge_in_use_problem(
    message: str, referrers: list[record_referrer_wire.RecordReferrer], *, instance: str
) -> forge_wire.ForgeInUseProblem:
    return forge_wire.ForgeInUseProblem(
        type=forge_wire.FORGE_IN_USE,
        title="Forge in use",
        detail=forge_wire.ForgeInUseDetail(message=message, referrers=referrers),
        instance=instance,
        status=409,
    )


def forge_revision_conflict_problem(
    message: str, current: int, *, instance: str
) -> forge_wire.ForgeRevisionConflictProblem:
    return forge_wire.ForgeRevisionConflictProblem(
        type=forge_wire.FORGE_REVISION_CONFLICT,
        title="Forge revision conflict",
        detail=forge_wire.ForgeRevisionConflictDetail(message=message, current=current),
        instance=instance,
        status=409,
    )


def forge_origin_taken_problem(
    message: str, holder: str, api_base: str, *, instance: str
) -> forge_wire.ForgeOriginTakenProblem:
    return forge_wire.ForgeOriginTakenProblem(
        type=forge_wire.FORGE_ORIGIN_TAKEN,
        title="Forge origin taken",
        detail=forge_wire.ForgeOriginTakenDetail(message=message, holder=holder, api_base=api_base),
        instance=instance,
        status=409,
    )


def forge_slug_taken_problem(message: str, slug: str, *, instance: str) -> forge_wire.ForgeSlugTakenProblem:
    return forge_wire.ForgeSlugTakenProblem(
        type=forge_wire.FORGE_SLUG_TAKEN,
        title="Forge slug taken",
        detail=forge_wire.ForgeSlugTakenDetail(message=message, slug=slug),
        instance=instance,
        status=409,
    )


def credential_not_found_problem(detail: str, *, instance: str) -> credential_wire.CredentialNotFoundProblem:
    return credential_wire.CredentialNotFoundProblem(
        type=credential_wire.CREDENTIAL_NOT_FOUND,
        title="Credential not found",
        detail=detail,
        instance=instance,
        status=404,
    )


def credential_in_use_problem(
    message: str, referrers: list[record_referrer_wire.RecordReferrer], *, instance: str
) -> credential_wire.CredentialInUseProblem:
    return credential_wire.CredentialInUseProblem(
        type=credential_wire.CREDENTIAL_IN_USE,
        title="Credential in use",
        detail=message,
        instance=instance,
        status=409,
        referrers=referrers,
    )


def credential_slug_taken_problem(
    message: str, slug: str, *, instance: str
) -> credential_wire.CredentialSlugTakenProblem:
    return credential_wire.CredentialSlugTakenProblem(
        type=credential_wire.CREDENTIAL_SLUG_TAKEN,
        title="Credential slug taken",
        detail=message,
        instance=instance,
        status=409,
        slug=slug,
    )


def credential_authority_taken_problem(
    message: str, holder: str, authority: str, *, instance: str
) -> credential_wire.CredentialAuthorityTakenProblem:
    return credential_wire.CredentialAuthorityTakenProblem(
        type=credential_wire.CREDENTIAL_AUTHORITY_TAKEN,
        title="Credential authority taken",
        detail=message,
        instance=instance,
        status=409,
        holder=holder,
        authority=authority,
    )


def credential_capability_missing_problem(
    message: str, referrers: list[record_referrer_wire.RecordReferrer], missing: list[str], *, instance: str
) -> credential_wire.CredentialCapabilityMissingProblem:
    return credential_wire.CredentialCapabilityMissingProblem(
        type=credential_wire.CREDENTIAL_CAPABILITY_MISSING,
        title="Credential capability missing",
        detail=message,
        instance=instance,
        status=422,
        referrers=referrers,
        missing=missing,
    )


def secret_in_use_problem(
    message: str, referrers: list[record_referrer_wire.RecordReferrer], *, instance: str
) -> secret_wire.SecretInUseProblem:
    return secret_wire.SecretInUseProblem(
        type=secret_wire.SECRET_IN_USE,
        title="Secret in use",
        detail=secret_wire.SecretInUseDetail(message=message, referrers=referrers),
        instance=instance,
        status=409,
    )


def config_field_invalid_problem(
    where: str, field: str, message: str, code: str, *, instance: str
) -> config_wire.ConfigFieldInvalidProblem:
    return config_wire.ConfigFieldInvalidProblem(
        type=config_wire.CONFIG_FIELD_INVALID,
        title="Configuration field invalid",
        detail=[ValidationEntry(loc=[where, field], msg=message, type=code)],
        instance=instance,
        status=422,
    )


def unknown_query_work_source_problem(value: str, *, instance: str) -> work_source_wire.UnknownWorkSourceProblem:
    return work_source_wire.UnknownWorkSourceProblem(
        type=work_source_wire.UNKNOWN_WORK_SOURCE,
        title="Unknown work source",
        detail=[
            work_source_wire.UnknownWorkSourceDetail(
                loc=["query", "source"], msg=f"unknown work source {value!r}", type="unknown_work_source"
            )
        ],
        instance=instance,
        status=422,
    )


def unknown_body_project_problem(field: str, value: str, *, instance: str) -> UnknownProjectProblem:
    """The unknown-project problem a request body's project field draws; the query's names its own location."""
    return UnknownProjectProblem(
        type=UNKNOWN_PROJECT,
        title="Unknown project",
        detail=[UnknownProjectDetail(loc=["body", field], msg=f"unknown project {value!r}", type="unknown_project")],
        instance=instance,
        status=422,
    )

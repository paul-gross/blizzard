"""Factories for the admin family's problems. Callers supply the already-sanitized instance and detail."""

from __future__ import annotations

from blizzard.wire import problem_types_admin as types
from blizzard.wire.problem import UNKNOWN_PROJECT, ValidationEntry
from blizzard.wire.project import UnknownProjectDetail, UnknownProjectProblem


def project_not_found_problem(detail: str, *, instance: str) -> types.ProjectNotFoundProblem:
    return types.ProjectNotFoundProblem(
        type=types.PROJECT_NOT_FOUND, title="Project not found", detail=detail, instance=instance, status=404
    )


def project_link_not_found_problem(detail: str, *, instance: str) -> types.ProjectLinkNotFoundProblem:
    return types.ProjectLinkNotFoundProblem(
        type=types.PROJECT_LINK_NOT_FOUND, title="Project link not found", detail=detail, instance=instance, status=404
    )


def forge_not_found_problem(detail: str, *, instance: str) -> types.ForgeNotFoundProblem:
    return types.ForgeNotFoundProblem(
        type=types.FORGE_NOT_FOUND, title="Forge not found", detail=detail, instance=instance, status=404
    )


def repository_not_found_problem(detail: str, *, instance: str) -> types.RepositoryNotFoundProblem:
    return types.RepositoryNotFoundProblem(
        type=types.REPOSITORY_NOT_FOUND, title="Repository not found", detail=detail, instance=instance, status=404
    )


def work_source_not_found_problem(detail: str, *, instance: str) -> types.WorkSourceNotFoundProblem:
    return types.WorkSourceNotFoundProblem(
        type=types.WORK_SOURCE_NOT_FOUND, title="Work source not found", detail=detail, instance=instance, status=404
    )


def graph_not_found_problem(detail: str, *, instance: str) -> types.GraphNotFoundProblem:
    return types.GraphNotFoundProblem(
        type=types.GRAPH_NOT_FOUND, title="Graph not found", detail=detail, instance=instance, status=404
    )


def config_schema_not_found_problem(detail: str, *, instance: str) -> types.ConfigSchemaNotFoundProblem:
    return types.ConfigSchemaNotFoundProblem(
        type=types.CONFIG_SCHEMA_NOT_FOUND,
        title="Config schema not found",
        detail=detail,
        instance=instance,
        status=404,
    )


def user_not_found_problem(detail: str, *, instance: str) -> types.UserNotFoundProblem:
    return types.UserNotFoundProblem(
        type=types.USER_NOT_FOUND, title="User not found", detail=detail, instance=instance, status=404
    )


def member_not_found_problem(detail: str, *, instance: str) -> types.MemberNotFoundProblem:
    return types.MemberNotFoundProblem(
        type=types.MEMBER_NOT_FOUND, title="Member not found", detail=detail, instance=instance, status=404
    )


def invitation_not_found_problem(detail: str, *, instance: str) -> types.InvitationNotFoundProblem:
    return types.InvitationNotFoundProblem(
        type=types.INVITATION_NOT_FOUND, title="Invitation not found", detail=detail, instance=instance, status=404
    )


def work_item_not_found_problem(detail: str, *, instance: str) -> types.WorkItemNotFoundProblem:
    return types.WorkItemNotFoundProblem(
        type=types.WORK_ITEM_NOT_FOUND, title="Work item not found", detail=detail, instance=instance, status=404
    )


def project_slug_taken_problem(detail: str, *, instance: str) -> types.ProjectSlugTakenProblem:
    return types.ProjectSlugTakenProblem(
        type=types.PROJECT_SLUG_TAKEN, title="Project slug taken", detail=detail, instance=instance, status=409
    )


def repository_slug_taken_problem(detail: str, *, instance: str) -> types.RepositorySlugTakenProblem:
    return types.RepositorySlugTakenProblem(
        type=types.REPOSITORY_SLUG_TAKEN, title="Repository slug taken", detail=detail, instance=instance, status=409
    )


def repository_coordinate_taken_problem(detail: str, *, instance: str) -> types.RepositoryCoordinateTakenProblem:
    return types.RepositoryCoordinateTakenProblem(
        type=types.REPOSITORY_COORDINATE_TAKEN,
        title="Repository coordinate taken",
        detail=detail,
        instance=instance,
        status=409,
    )


def work_source_slug_taken_problem(detail: str, *, instance: str) -> types.WorkSourceSlugTakenProblem:
    return types.WorkSourceSlugTakenProblem(
        type=types.WORK_SOURCE_SLUG_TAKEN, title="Work source slug taken", detail=detail, instance=instance, status=409
    )


def work_source_locator_taken_problem(detail: str, *, instance: str) -> types.WorkSourceLocatorTakenProblem:
    return types.WorkSourceLocatorTakenProblem(
        type=types.WORK_SOURCE_LOCATOR_TAKEN,
        title="Work source locator taken",
        detail=detail,
        instance=instance,
        status=409,
    )


def work_source_no_editor_problem(detail: str, *, instance: str) -> types.WorkSourceNoEditorProblem:
    return types.WorkSourceNoEditorProblem(
        type=types.WORK_SOURCE_NO_EDITOR, title="Work source no editor", detail=detail, instance=instance, status=409
    )


def work_item_not_editable_problem(detail: str, *, instance: str) -> types.WorkItemNotEditableProblem:
    return types.WorkItemNotEditableProblem(
        type=types.WORK_ITEM_NOT_EDITABLE, title="Work item not editable", detail=detail, instance=instance, status=409
    )


def work_item_held_by_live_chunk_problem(detail: str, *, instance: str) -> types.WorkItemHeldByLiveChunkProblem:
    return types.WorkItemHeldByLiveChunkProblem(
        type=types.WORK_ITEM_HELD_BY_LIVE_CHUNK,
        title="Work item held by live chunk",
        detail=detail,
        instance=instance,
        status=409,
    )


def work_item_held_by_dependents_problem(detail: str, *, instance: str) -> types.WorkItemHeldByDependentsProblem:
    return types.WorkItemHeldByDependentsProblem(
        type=types.WORK_ITEM_HELD_BY_DEPENDENTS,
        title="Work item held by dependents",
        detail=detail,
        instance=instance,
        status=409,
    )


def work_item_field_blank_problem(detail: str, *, instance: str) -> types.WorkItemFieldBlankProblem:
    return types.WorkItemFieldBlankProblem(
        type=types.WORK_ITEM_FIELD_BLANK, title="Work item field blank", detail=detail, instance=instance, status=422
    )


def default_graph_retired_problem(detail: str, *, instance: str) -> types.DefaultGraphRetiredProblem:
    return types.DefaultGraphRetiredProblem(
        type=types.DEFAULT_GRAPH_RETIRED, title="Default graph retired", detail=detail, instance=instance, status=503
    )


def secret_retired_problem(detail: str, *, instance: str) -> types.SecretRetiredProblem:
    return types.SecretRetiredProblem(
        type=types.SECRET_RETIRED, title="Secret retired", detail=detail, instance=instance, status=409
    )


def secret_name_invalid_problem(detail: str, *, instance: str) -> types.SecretNameInvalidProblem:
    return types.SecretNameInvalidProblem(
        type=types.SECRET_NAME_INVALID, title="Secret name invalid", detail=detail, instance=instance, status=422
    )


def secret_value_blank_problem(detail: str, *, instance: str) -> types.SecretValueBlankProblem:
    return types.SecretValueBlankProblem(
        type=types.SECRET_VALUE_BLANK, title="Secret value blank", detail=detail, instance=instance, status=422
    )


def invitation_already_resolved_problem(detail: str, *, instance: str) -> types.InvitationAlreadyResolvedProblem:
    return types.InvitationAlreadyResolvedProblem(
        type=types.INVITATION_ALREADY_RESOLVED,
        title="Invitation already resolved",
        detail=detail,
        instance=instance,
        status=409,
    )


def last_hub_admin_problem(detail: str, *, instance: str) -> types.LastHubAdminProblem:
    return types.LastHubAdminProblem(
        type=types.LAST_HUB_ADMIN, title="Last hub admin", detail=detail, instance=instance, status=409
    )


def last_admin_kept_problem(detail: str, *, instance: str) -> types.LastAdminKeptProblem:
    return types.LastAdminKeptProblem(
        type=types.LAST_ADMIN_KEPT, title="Last admin kept", detail=detail, instance=instance, status=409
    )


def membership_change_refused_problem(detail: str, *, instance: str) -> types.MembershipChangeRefusedProblem:
    return types.MembershipChangeRefusedProblem(
        type=types.MEMBERSHIP_CHANGE_REFUSED,
        title="Membership change refused",
        detail=detail,
        instance=instance,
        status=403,
    )


def role_not_assignable_problem(detail: str, *, instance: str) -> types.RoleNotAssignableProblem:
    return types.RoleNotAssignableProblem(
        type=types.ROLE_NOT_ASSIGNABLE, title="Role not assignable", detail=detail, instance=instance, status=403
    )


def unknown_role_problem(detail: str, *, instance: str) -> types.UnknownRoleProblem:
    return types.UnknownRoleProblem(
        type=types.UNKNOWN_ROLE, title="Unknown role", detail=detail, instance=instance, status=400
    )


def tenant_name_refused_problem(detail: str, *, instance: str) -> types.TenantNameRefusedProblem:
    return types.TenantNameRefusedProblem(
        type=types.TENANT_NAME_REFUSED, title="Tenant name refused", detail=detail, instance=instance, status=422
    )


def tenant_deleted_problem(detail: str, *, instance: str) -> types.TenantDeletedProblem:
    return types.TenantDeletedProblem(
        type=types.TENANT_DELETED, title="Tenant deleted", detail=detail, instance=instance, status=410
    )


def landing_order_conflict_problem(message: str, *, instance: str) -> types.LandingOrderConflictProblem:
    return types.LandingOrderConflictProblem(
        type=types.LANDING_ORDER_CONFLICT,
        title="Landing order conflict",
        detail=types.LandingOrderConflictDetail(message=message),
        instance=instance,
        status=409,
    )


def last_tenant_problem(*, instance: str) -> types.LastTenantProblem:
    return types.LastTenantProblem(
        type=types.LAST_TENANT,
        title="Last tenant",
        detail=types.LastTenantDetail(message="the hub's only open tenant cannot be deleted"),
        instance=instance,
        status=409,
    )


def forge_in_use_problem(
    message: str, referrers: list[types.RecordReferrer], *, instance: str
) -> types.ForgeInUseProblem:
    return types.ForgeInUseProblem(
        type=types.FORGE_IN_USE,
        title="Forge in use",
        detail=types.ForgeInUseDetail(message=message, referrers=referrers),
        instance=instance,
        status=409,
    )


def forge_revision_conflict_problem(message: str, current: int, *, instance: str) -> types.ForgeRevisionConflictProblem:
    return types.ForgeRevisionConflictProblem(
        type=types.FORGE_REVISION_CONFLICT,
        title="Forge revision conflict",
        detail=types.ForgeRevisionConflictDetail(message=message, current=current),
        instance=instance,
        status=409,
    )


def forge_origin_taken_problem(
    message: str, holder: str, api_base: str, *, instance: str
) -> types.ForgeOriginTakenProblem:
    return types.ForgeOriginTakenProblem(
        type=types.FORGE_ORIGIN_TAKEN,
        title="Forge origin taken",
        detail=types.ForgeOriginTakenDetail(message=message, holder=holder, api_base=api_base),
        instance=instance,
        status=409,
    )


def forge_slug_taken_problem(message: str, slug: str, *, instance: str) -> types.ForgeSlugTakenProblem:
    return types.ForgeSlugTakenProblem(
        type=types.FORGE_SLUG_TAKEN,
        title="Forge slug taken",
        detail=types.ForgeSlugTakenDetail(message=message, slug=slug),
        instance=instance,
        status=409,
    )


def secret_in_use_problem(
    message: str, referrers: list[types.RecordReferrer], *, instance: str
) -> types.SecretInUseProblem:
    return types.SecretInUseProblem(
        type=types.SECRET_IN_USE,
        title="Secret in use",
        detail=types.SecretInUseDetail(message=message, referrers=referrers),
        instance=instance,
        status=409,
    )


def config_field_invalid_problem(
    where: str, field: str, message: str, code: str, *, instance: str
) -> types.ConfigFieldInvalidProblem:
    return types.ConfigFieldInvalidProblem(
        type=types.CONFIG_FIELD_INVALID,
        title="Configuration field invalid",
        detail=[ValidationEntry(loc=[where, field], msg=message, type=code)],
        instance=instance,
        status=422,
    )


def unknown_query_work_source_problem(value: str, *, instance: str) -> types.UnknownWorkSourceProblem:
    return types.UnknownWorkSourceProblem(
        type=types.UNKNOWN_WORK_SOURCE,
        title="Unknown work source",
        detail=[
            types.UnknownWorkSourceDetail(
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

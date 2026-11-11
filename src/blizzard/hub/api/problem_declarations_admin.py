"""The problem variants the admin family's operations declare: configuration records, secrets, members, invitations,
tenants, hub administrators, users and graphs."""

from __future__ import annotations

from blizzard.hub.api.problem_declaration import ProblemDeclaration
from blizzard.wire import config as config_wire
from blizzard.wire import forge as forge_wire
from blizzard.wire import graph as graph_wire
from blizzard.wire import invitation as invitation_wire
from blizzard.wire import project as project_wire
from blizzard.wire import repository as repository_wire
from blizzard.wire import secret as secret_wire
from blizzard.wire import tenant as tenant_wire
from blizzard.wire import urgency as urgency_wire
from blizzard.wire import user as user_wire
from blizzard.wire import work_source as work_source_wire
from blizzard.wire.cadence import RevisionConflictProblem
from blizzard.wire.chunk import ChunkNotFoundProblem
from blizzard.wire.project import UnknownProjectProblem

#: Every operation whose ``{project}`` path segment is resolved by ``path_project``.
_PROJECT_PATH_OPERATIONS: tuple[tuple[str, str], ...] = (
    ("GET", "/api/projects/{project}"),
    ("PATCH", "/api/projects/{project}"),
    ("GET", "/api/projects/{project}/source-links"),
    ("PUT", "/api/projects/{project}/source-links/{source}"),
    ("POST", "/api/projects/{project}/source-links/{source}/retire"),
    ("GET", "/api/projects/{project}/repository-links"),
    ("PUT", "/api/projects/{project}/repository-links/{repository}"),
    ("PATCH", "/api/projects/{project}/repository-links/{repository}"),
    ("POST", "/api/projects/{project}/repository-links/{repository}/retire"),
    ("PUT", "/api/projects/{project}/landing-order"),
    ("POST", "/api/projects/{project}/landing-order/position"),
    ("GET", "/api/projects/{project}/cadences"),
    ("POST", "/api/projects/{project}/cadences"),
    ("GET", "/api/projects/{project}/findings/classes"),
    ("GET", "/api/projects/{project}/findings"),
    ("GET", "/api/projects/{project}/garden-proposals"),
    ("POST", "/api/projects/{project}/garden-proposals"),
    ("GET", "/api/projects/{project}/runs/{chunk_id}"),
    ("GET", "/api/projects/{project}/runs"),
    ("GET", "/api/projects/{project}/routines"),
    ("POST", "/api/projects/{project}/routines"),
    ("GET", "/api/projects/{project}/scopes"),
    ("GET", "/api/projects/{project}/scopes/{slug}"),
    ("GET", "/api/projects/{project}/scopes/{slug}/routines"),
    ("PATCH", "/api/projects/{project}/scopes/{slug}"),
    ("POST", "/api/projects/{project}/scopes"),
    ("POST", "/api/projects/{project}/scopes/{slug}/enable"),
    ("POST", "/api/projects/{project}/scopes/{slug}/retire"),
)

_ANALYTICS_SOURCE_PATHS = (
    "/api/analytics/events",
    "/api/analytics/events/ndjson",
    "/api/analytics/counts/files",
    "/api/analytics/counts/skills",
    "/api/analytics/counts/agent-types",
    "/api/analytics/counts/nodes",
    "/api/analytics/durations/nodes",
    "/api/analytics/durations/graphs",
    "/api/analytics/spend/nodes",
    "/api/analytics/spend/graphs",
    "/api/analytics/spend/models",
    "/api/analytics/spend/chunks",
    "/api/analytics/spend/chunks/ndjson",
    "/api/analytics/outcomes/nodes",
)

_WORK_SOURCE_ITEM_PATHS = (
    ("GET", "/api/work-sources/{source}/items"),
    ("POST", "/api/work-sources/{source}/items"),
    ("GET", "/api/work-sources/{source}/items/{ref}"),
    ("PATCH", "/api/work-sources/{source}/items/{ref}"),
    ("DELETE", "/api/work-sources/{source}/items/{ref}"),
)

_REVISION_CONFLICT_OPERATIONS = (
    ("PATCH", "/api/projects/{project}"),
    ("PUT", "/api/projects/{project}/source-links/{source}"),
    ("POST", "/api/projects/{project}/source-links/{source}/retire"),
    ("PUT", "/api/projects/{project}/repository-links/{repository}"),
    ("PATCH", "/api/projects/{project}/repository-links/{repository}"),
    ("POST", "/api/projects/{project}/repository-links/{repository}/retire"),
    ("PATCH", "/api/repositories/{repository}"),
    ("POST", "/api/repositories/{repository}/retire"),
    ("POST", "/api/repositories/{repository}/enable"),
    ("PATCH", "/api/work-sources/{source}"),
    ("POST", "/api/work-sources/{source}/retire"),
    ("POST", "/api/work-sources/{source}/enable"),
    ("PUT", "/api/secrets/{name}/value"),
    ("PATCH", "/api/urgencies/{key}"),
    ("POST", "/api/urgencies/{key}/retire"),
    ("POST", "/api/urgencies/{key}/enable"),
    ("PATCH", "/api/settings"),
)

_FIELD_INVALID_OPERATIONS = (
    ("POST", "/api/projects"),
    ("PATCH", "/api/projects/{project}"),
    ("PATCH", "/api/projects/{project}/repository-links/{repository}"),
    ("PUT", "/api/projects/{project}/landing-order"),
    ("POST", "/api/projects/{project}/landing-order/position"),
    ("POST", "/api/forges"),
    ("PATCH", "/api/forges/{forge}"),
    ("POST", "/api/repositories"),
    ("PATCH", "/api/repositories/{repository}"),
    ("POST", "/api/repositories/{repository}/enable"),
    ("POST", "/api/work-sources"),
    ("PATCH", "/api/work-sources/{source}"),
    ("POST", "/api/work-sources/{source}/enable"),
    ("POST", "/api/urgencies"),
    ("PATCH", "/api/urgencies/{key}"),
    ("POST", "/api/urgencies/{key}/retire"),
    ("PUT", "/api/urgencies/order"),
    ("POST", "/api/urgencies/order/position"),
    ("PATCH", "/api/settings"),
)

_URGENCY_KEY_OPERATIONS = (
    ("GET", "/api/urgencies/{key}"),
    ("PATCH", "/api/urgencies/{key}"),
    ("POST", "/api/urgencies/{key}/retire"),
    ("POST", "/api/urgencies/{key}/enable"),
    ("GET", "/api/urgencies/{key}/icon"),
)

_SECRET_NAME_OPERATIONS = (
    ("POST", "/api/secrets"),
    ("PUT", "/api/secrets/{name}/value"),
    ("GET", "/api/secrets/{name}"),
    ("POST", "/api/secrets/{name}/retire"),
    ("POST", "/api/secrets/{name}/enable"),
)

_MEMBERSHIP_CHANGE_OPERATIONS = (
    ("PUT", "/api/members/{user_id}"),
    ("DELETE", "/api/members/{user_id}"),
    ("POST", "/api/users/{user_id}/role"),
)

ADMIN_FAMILY_DECLARATIONS: tuple[ProblemDeclaration, ...] = (
    *(
        ProblemDeclaration(method, path, 404, project_wire.ProjectNotFoundProblem)
        for method, path in _PROJECT_PATH_OPERATIONS
    ),
    ProblemDeclaration("POST", "/api/chunks", 422, UnknownProjectProblem),
    *(ProblemDeclaration(method, path, 409, RevisionConflictProblem) for method, path in _REVISION_CONFLICT_OPERATIONS),
    *(
        ProblemDeclaration(method, path, 422, config_wire.ConfigFieldInvalidProblem)
        for method, path in _FIELD_INVALID_OPERATIONS
    ),
    ProblemDeclaration("POST", "/api/projects", 409, project_wire.ProjectSlugTakenProblem),
    ProblemDeclaration("PATCH", "/api/projects/{project}", 409, project_wire.ProjectSlugTakenProblem),
    ProblemDeclaration(
        "POST", "/api/projects/{project}/source-links/{source}/retire", 404, project_wire.ProjectLinkNotFoundProblem
    ),
    ProblemDeclaration(
        "POST",
        "/api/projects/{project}/repository-links/{repository}/retire",
        404,
        project_wire.ProjectLinkNotFoundProblem,
    ),
    ProblemDeclaration(
        "PATCH",
        "/api/projects/{project}/repository-links/{repository}",
        404,
        project_wire.ProjectLinkNotFoundProblem,
    ),
    ProblemDeclaration(
        "PUT", "/api/projects/{project}/source-links/{source}", 404, work_source_wire.WorkSourceNotFoundProblem
    ),
    ProblemDeclaration(
        "POST", "/api/projects/{project}/source-links/{source}/retire", 404, work_source_wire.WorkSourceNotFoundProblem
    ),
    ProblemDeclaration(
        "PUT", "/api/projects/{project}/repository-links/{repository}", 404, repository_wire.RepositoryNotFoundProblem
    ),
    ProblemDeclaration(
        "PATCH", "/api/projects/{project}/repository-links/{repository}", 404, repository_wire.RepositoryNotFoundProblem
    ),
    ProblemDeclaration(
        "POST",
        "/api/projects/{project}/repository-links/{repository}/retire",
        404,
        repository_wire.RepositoryNotFoundProblem,
    ),
    ProblemDeclaration("PUT", "/api/projects/{project}/landing-order", 409, project_wire.LandingOrderConflictProblem),
    ProblemDeclaration(
        "POST", "/api/projects/{project}/landing-order/position", 409, project_wire.LandingOrderConflictProblem
    ),
    *(
        ProblemDeclaration(method, path, 404, forge_wire.ForgeNotFoundProblem)
        for method, path in (
            ("GET", "/api/forges/{forge}"),
            ("PATCH", "/api/forges/{forge}"),
            ("POST", "/api/forges/{forge}/retire"),
            ("POST", "/api/forges/{forge}/enable"),
        )
    ),
    *(
        ProblemDeclaration(method, path, 409, model)
        for method, path, model in (
            ("POST", "/api/forges", forge_wire.ForgeSlugTakenProblem),
            ("POST", "/api/forges", forge_wire.ForgeOriginTakenProblem),
            ("PATCH", "/api/forges/{forge}", forge_wire.ForgeRevisionConflictProblem),
            ("PATCH", "/api/forges/{forge}", forge_wire.ForgeSlugTakenProblem),
            ("PATCH", "/api/forges/{forge}", forge_wire.ForgeOriginTakenProblem),
            ("POST", "/api/forges/{forge}/retire", forge_wire.ForgeRevisionConflictProblem),
            ("POST", "/api/forges/{forge}/retire", forge_wire.ForgeInUseProblem),
            ("POST", "/api/forges/{forge}/enable", forge_wire.ForgeRevisionConflictProblem),
        )
    ),
    *(
        ProblemDeclaration(method, path, 404, urgency_wire.UrgencyNotFoundProblem)
        for method, path in _URGENCY_KEY_OPERATIONS
    ),
    ProblemDeclaration("GET", "/api/urgencies/{key}/icon", 404, urgency_wire.UrgencyIconNotFoundProblem),
    ProblemDeclaration("POST", "/api/urgencies/{key}/retire", 409, urgency_wire.UrgencyIsDefaultProblem),
    ProblemDeclaration("POST", "/api/urgencies/{key}/retire", 409, urgency_wire.UrgencyInUseProblem),
    *(
        ProblemDeclaration(method, path, 404, repository_wire.RepositoryNotFoundProblem)
        for method, path in (
            ("GET", "/api/repositories/{repository}"),
            ("PATCH", "/api/repositories/{repository}"),
            ("POST", "/api/repositories/{repository}/retire"),
            ("POST", "/api/repositories/{repository}/enable"),
        )
    ),
    *(
        ProblemDeclaration(method, path, 409, model)
        for method, path, model in (
            ("POST", "/api/repositories", repository_wire.RepositorySlugTakenProblem),
            ("POST", "/api/repositories", repository_wire.RepositoryCoordinateTakenProblem),
            ("PATCH", "/api/repositories/{repository}", repository_wire.RepositorySlugTakenProblem),
            ("PATCH", "/api/repositories/{repository}", repository_wire.RepositoryCoordinateTakenProblem),
        )
    ),
    *(
        ProblemDeclaration(method, path, 404, work_source_wire.WorkSourceNotFoundProblem)
        for method, path in (
            ("GET", "/api/work-sources/{source}"),
            ("PATCH", "/api/work-sources/{source}"),
            ("POST", "/api/work-sources/{source}/retire"),
            ("POST", "/api/work-sources/{source}/enable"),
            *_WORK_SOURCE_ITEM_PATHS,
        )
    ),
    *(
        ProblemDeclaration(method, path, 409, model)
        for method, path, model in (
            ("POST", "/api/work-sources", work_source_wire.WorkSourceSlugTakenProblem),
            ("POST", "/api/work-sources", work_source_wire.WorkSourceLocatorTakenProblem),
            ("PATCH", "/api/work-sources/{source}", work_source_wire.WorkSourceSlugTakenProblem),
            ("PATCH", "/api/work-sources/{source}", work_source_wire.WorkSourceLocatorTakenProblem),
        )
    ),
    *(
        ProblemDeclaration(method, path, 409, work_source_wire.WorkSourceNoEditorProblem)
        for method, path in _WORK_SOURCE_ITEM_PATHS
    ),
    ProblemDeclaration("POST", "/api/work-sources/{source}/items", 422, work_source_wire.WorkItemFieldBlankProblem),
    ProblemDeclaration(
        "PATCH", "/api/work-sources/{source}/items/{ref}", 422, work_source_wire.WorkItemFieldBlankProblem
    ),
    ProblemDeclaration("POST", "/api/work-sources/{source}/items", 503, graph_wire.DefaultGraphRetiredProblem),
    *(
        ProblemDeclaration(method, path, 404, work_source_wire.WorkItemNotFoundProblem)
        for method, path in (
            ("GET", "/api/work-sources/{source}/items/{ref}"),
            ("PATCH", "/api/work-sources/{source}/items/{ref}"),
            ("DELETE", "/api/work-sources/{source}/items/{ref}"),
        )
    ),
    ProblemDeclaration("DELETE", "/api/work-sources/{source}/items/{ref}", 404, ChunkNotFoundProblem),
    ProblemDeclaration(
        "PATCH", "/api/work-sources/{source}/items/{ref}", 409, work_source_wire.WorkItemNotEditableProblem
    ),
    ProblemDeclaration(
        "DELETE", "/api/work-sources/{source}/items/{ref}", 409, work_source_wire.WorkItemNotEditableProblem
    ),
    ProblemDeclaration(
        "DELETE", "/api/work-sources/{source}/items/{ref}", 409, work_source_wire.WorkItemHeldByLiveChunkProblem
    ),
    ProblemDeclaration(
        "DELETE", "/api/work-sources/{source}/items/{ref}", 409, work_source_wire.WorkItemHeldByDependentsProblem
    ),
    *(
        ProblemDeclaration("GET", path, 422, work_source_wire.UnknownWorkSourceProblem)
        for path in _ANALYTICS_SOURCE_PATHS
    ),
    *(
        ProblemDeclaration(method, path, 422, secret_wire.SecretNameInvalidProblem)
        for method, path in _SECRET_NAME_OPERATIONS
    ),
    ProblemDeclaration("POST", "/api/secrets", 422, secret_wire.SecretValueBlankProblem),
    ProblemDeclaration("PUT", "/api/secrets/{name}/value", 422, secret_wire.SecretValueBlankProblem),
    ProblemDeclaration("PUT", "/api/secrets/{name}/value", 409, secret_wire.SecretRetiredProblem),
    ProblemDeclaration("POST", "/api/secrets/{name}/retire", 409, secret_wire.SecretInUseProblem),
    ProblemDeclaration("GET", "/api/config/schema/{kind}", 404, config_wire.ConfigSchemaNotFoundProblem),
    *(
        ProblemDeclaration(method, path, 404, graph_wire.GraphNotFoundProblem)
        for method, path in (
            ("GET", "/api/graphs/{graph_id}"),
            ("POST", "/api/graphs/{graph_id}/retire"),
            ("POST", "/api/graphs/{graph_id}/enable"),
            ("PATCH", "/api/graphs/{graph_id}"),
            ("POST", "/api/graphs/{graph_id}/follow-latest"),
            ("PATCH", "/api/chunks/{chunk_id}"),
            ("POST", "/api/chunks/{chunk_id}/restart"),
        )
    ),
    ProblemDeclaration("POST", "/api/admin/tenants", 422, tenant_wire.TenantNameRefusedProblem),
    ProblemDeclaration("POST", "/api/admin/tenants", 404, user_wire.UserNotFoundProblem),
    ProblemDeclaration("PATCH", "/api/admin/tenants/{tenant_id}", 422, tenant_wire.TenantNameRefusedProblem),
    ProblemDeclaration("DELETE", "/api/admin/tenants/{tenant_id}", 409, tenant_wire.LastTenantProblem),
    ProblemDeclaration("DELETE", "/api/admin/tenants/{tenant_id}", 410, tenant_wire.TenantDeletedProblem),
    ProblemDeclaration("PUT", "/api/admin/hub-admins/{user_id}", 404, user_wire.UserNotFoundProblem),
    ProblemDeclaration("DELETE", "/api/admin/hub-admins/{user_id}", 404, user_wire.UserNotFoundProblem),
    ProblemDeclaration("DELETE", "/api/admin/hub-admins/{user_id}", 409, user_wire.LastHubAdminProblem),
    ProblemDeclaration(
        "POST", "/api/invitations/{invitation_id}/revocations", 404, invitation_wire.InvitationNotFoundProblem
    ),
    ProblemDeclaration(
        "POST", "/api/invitations/{invitation_id}/revocations", 409, invitation_wire.InvitationAlreadyResolvedProblem
    ),
    *(
        ProblemDeclaration(method, path, status, model)
        for method, path in _MEMBERSHIP_CHANGE_OPERATIONS
        for status, model in (
            (404, user_wire.MemberNotFoundProblem),
            (403, user_wire.MembershipChangeRefusedProblem),
            (409, user_wire.LastAdminKeptProblem),
        )
    ),
    ProblemDeclaration("POST", "/api/users/{user_id}/role", 400, user_wire.UnknownRoleProblem),
    ProblemDeclaration("POST", "/api/users/{user_id}/role", 403, user_wire.RoleNotAssignableProblem),
)

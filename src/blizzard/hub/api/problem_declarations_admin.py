"""The problem variants the admin family's operations declare: configuration records, secrets, members, invitations,
tenants, hub administrators, users and graphs."""

from __future__ import annotations

from blizzard.hub.api.problem_declaration import ProblemDeclaration
from blizzard.wire import problem_types_admin as t
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
    ("POST", "/api/projects/{project}/repository-links/{repository}/retire"),
    ("PATCH", "/api/repositories/{repository}"),
    ("POST", "/api/repositories/{repository}/retire"),
    ("POST", "/api/repositories/{repository}/enable"),
    ("PATCH", "/api/work-sources/{source}"),
    ("POST", "/api/work-sources/{source}/retire"),
    ("POST", "/api/work-sources/{source}/enable"),
    ("PUT", "/api/secrets/{name}/value"),
)

_FIELD_INVALID_OPERATIONS = (
    ("POST", "/api/projects"),
    ("PATCH", "/api/projects/{project}"),
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
    *(ProblemDeclaration(method, path, 404, t.ProjectNotFoundProblem) for method, path in _PROJECT_PATH_OPERATIONS),
    ProblemDeclaration("POST", "/api/chunks", 422, UnknownProjectProblem),
    *(ProblemDeclaration(method, path, 409, RevisionConflictProblem) for method, path in _REVISION_CONFLICT_OPERATIONS),
    *(ProblemDeclaration(method, path, 422, t.ConfigFieldInvalidProblem) for method, path in _FIELD_INVALID_OPERATIONS),
    ProblemDeclaration("POST", "/api/projects", 409, t.ProjectSlugTakenProblem),
    ProblemDeclaration("PATCH", "/api/projects/{project}", 409, t.ProjectSlugTakenProblem),
    ProblemDeclaration(
        "POST", "/api/projects/{project}/source-links/{source}/retire", 404, t.ProjectLinkNotFoundProblem
    ),
    ProblemDeclaration(
        "POST", "/api/projects/{project}/repository-links/{repository}/retire", 404, t.ProjectLinkNotFoundProblem
    ),
    ProblemDeclaration("PUT", "/api/projects/{project}/source-links/{source}", 404, t.WorkSourceNotFoundProblem),
    ProblemDeclaration(
        "POST", "/api/projects/{project}/source-links/{source}/retire", 404, t.WorkSourceNotFoundProblem
    ),
    ProblemDeclaration(
        "PUT", "/api/projects/{project}/repository-links/{repository}", 404, t.RepositoryNotFoundProblem
    ),
    ProblemDeclaration(
        "POST", "/api/projects/{project}/repository-links/{repository}/retire", 404, t.RepositoryNotFoundProblem
    ),
    ProblemDeclaration("PUT", "/api/projects/{project}/landing-order", 409, t.LandingOrderConflictProblem),
    ProblemDeclaration("POST", "/api/projects/{project}/landing-order/position", 409, t.LandingOrderConflictProblem),
    *(
        ProblemDeclaration(method, path, 404, t.ForgeNotFoundProblem)
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
            ("POST", "/api/forges", t.ForgeSlugTakenProblem),
            ("POST", "/api/forges", t.ForgeOriginTakenProblem),
            ("PATCH", "/api/forges/{forge}", t.ForgeRevisionConflictProblem),
            ("PATCH", "/api/forges/{forge}", t.ForgeSlugTakenProblem),
            ("PATCH", "/api/forges/{forge}", t.ForgeOriginTakenProblem),
            ("POST", "/api/forges/{forge}/retire", t.ForgeRevisionConflictProblem),
            ("POST", "/api/forges/{forge}/retire", t.ForgeInUseProblem),
            ("POST", "/api/forges/{forge}/enable", t.ForgeRevisionConflictProblem),
        )
    ),
    *(
        ProblemDeclaration(method, path, 404, t.RepositoryNotFoundProblem)
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
            ("POST", "/api/repositories", t.RepositorySlugTakenProblem),
            ("POST", "/api/repositories", t.RepositoryCoordinateTakenProblem),
            ("PATCH", "/api/repositories/{repository}", t.RepositorySlugTakenProblem),
            ("PATCH", "/api/repositories/{repository}", t.RepositoryCoordinateTakenProblem),
        )
    ),
    *(
        ProblemDeclaration(method, path, 404, t.WorkSourceNotFoundProblem)
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
            ("POST", "/api/work-sources", t.WorkSourceSlugTakenProblem),
            ("POST", "/api/work-sources", t.WorkSourceLocatorTakenProblem),
            ("PATCH", "/api/work-sources/{source}", t.WorkSourceSlugTakenProblem),
            ("PATCH", "/api/work-sources/{source}", t.WorkSourceLocatorTakenProblem),
        )
    ),
    *(ProblemDeclaration(method, path, 409, t.WorkSourceNoEditorProblem) for method, path in _WORK_SOURCE_ITEM_PATHS),
    ProblemDeclaration("POST", "/api/work-sources/{source}/items", 422, t.WorkItemFieldBlankProblem),
    ProblemDeclaration("PATCH", "/api/work-sources/{source}/items/{ref}", 422, t.WorkItemFieldBlankProblem),
    ProblemDeclaration("POST", "/api/work-sources/{source}/items", 503, t.DefaultGraphRetiredProblem),
    *(
        ProblemDeclaration(method, path, 404, t.WorkItemNotFoundProblem)
        for method, path in (
            ("GET", "/api/work-sources/{source}/items/{ref}"),
            ("PATCH", "/api/work-sources/{source}/items/{ref}"),
            ("DELETE", "/api/work-sources/{source}/items/{ref}"),
        )
    ),
    ProblemDeclaration("DELETE", "/api/work-sources/{source}/items/{ref}", 404, ChunkNotFoundProblem),
    ProblemDeclaration("PATCH", "/api/work-sources/{source}/items/{ref}", 409, t.WorkItemNotEditableProblem),
    ProblemDeclaration("DELETE", "/api/work-sources/{source}/items/{ref}", 409, t.WorkItemNotEditableProblem),
    ProblemDeclaration("DELETE", "/api/work-sources/{source}/items/{ref}", 409, t.WorkItemHeldByLiveChunkProblem),
    ProblemDeclaration("DELETE", "/api/work-sources/{source}/items/{ref}", 409, t.WorkItemHeldByDependentsProblem),
    *(ProblemDeclaration("GET", path, 422, t.UnknownWorkSourceProblem) for path in _ANALYTICS_SOURCE_PATHS),
    *(ProblemDeclaration(method, path, 422, t.SecretNameInvalidProblem) for method, path in _SECRET_NAME_OPERATIONS),
    ProblemDeclaration("POST", "/api/secrets", 422, t.SecretValueBlankProblem),
    ProblemDeclaration("PUT", "/api/secrets/{name}/value", 422, t.SecretValueBlankProblem),
    ProblemDeclaration("PUT", "/api/secrets/{name}/value", 409, t.SecretRetiredProblem),
    ProblemDeclaration("POST", "/api/secrets/{name}/retire", 409, t.SecretInUseProblem),
    ProblemDeclaration("GET", "/api/config/schema/{kind}", 404, t.ConfigSchemaNotFoundProblem),
    *(
        ProblemDeclaration(method, path, 404, t.GraphNotFoundProblem)
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
    ProblemDeclaration("POST", "/api/admin/tenants", 422, t.TenantNameRefusedProblem),
    ProblemDeclaration("POST", "/api/admin/tenants", 404, t.UserNotFoundProblem),
    ProblemDeclaration("PATCH", "/api/admin/tenants/{tenant_id}", 422, t.TenantNameRefusedProblem),
    ProblemDeclaration("DELETE", "/api/admin/tenants/{tenant_id}", 409, t.LastTenantProblem),
    ProblemDeclaration("DELETE", "/api/admin/tenants/{tenant_id}", 410, t.TenantDeletedProblem),
    ProblemDeclaration("PUT", "/api/admin/hub-admins/{user_id}", 404, t.UserNotFoundProblem),
    ProblemDeclaration("DELETE", "/api/admin/hub-admins/{user_id}", 404, t.UserNotFoundProblem),
    ProblemDeclaration("DELETE", "/api/admin/hub-admins/{user_id}", 409, t.LastHubAdminProblem),
    ProblemDeclaration("POST", "/api/invitations/{invitation_id}/revocations", 404, t.InvitationNotFoundProblem),
    ProblemDeclaration("POST", "/api/invitations/{invitation_id}/revocations", 409, t.InvitationAlreadyResolvedProblem),
    *(
        ProblemDeclaration(method, path, status, model)
        for method, path in _MEMBERSHIP_CHANGE_OPERATIONS
        for status, model in (
            (404, t.MemberNotFoundProblem),
            (403, t.MembershipChangeRefusedProblem),
            (409, t.LastAdminKeptProblem),
        )
    ),
    ProblemDeclaration("POST", "/api/users/{user_id}/role", 400, t.UnknownRoleProblem),
    ProblemDeclaration("POST", "/api/users/{user_id}/role", 403, t.RoleNotAssignableProblem),
)

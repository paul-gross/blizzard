"""Which problem variants each hub operation declares, by status — the registry the
exception handlers and the OpenAPI document both read."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from fastapi import Request
from pydantic import BaseModel

from blizzard.foundation.roles import dto
from blizzard.wire.auth import (
    AuthenticationRequiredProblem,
    AuthenticationRevokedProblem,
    AuthenticationUnresolvedProblem,
    CliPkceRequiredProblem,
    CliRedirectRefusedProblem,
    CliTokenRefusedProblem,
    FederationClientRefusedProblem,
    FederationNoProviderProblem,
    FederationResponseModeProblem,
    IdpDisabledProblem,
    LoginDisabledProblem,
    LoginFailedProblem,
    LoginProviderUnknownProblem,
    LoginThrottledProblem,
    MachineCredentialForbiddenProblem,
    PermissionRequiredProblem,
    RunnerCredentialForbiddenProblem,
    SsoRefusedProblem,
)
from blizzard.wire.cadence import RevisionConflictProblem
from blizzard.wire.chunk import (
    ChunkIngestConflict,
    ChunkNotFoundProblem,
    ChunkNotPausableProblem,
    DependencyWouldCloseCycleView,
    DependentNotEditableView,
    FindingNotInChunkProblem,
    NoAnsweredProposalProblem,
    NoCurrentNodeProblem,
    NoRunContextProblem,
    NoStandingDependencyView,
    PrerequisiteIsEphemeralView,
)
from blizzard.wire.config import (
    ConfigApplyConflictProblem,
    ConfigDocumentInvalidProblem,
    ConfigMediaTypeUnsupportedProblem,
)
from blizzard.wire.decision import DecisionResolutionConflict
from blizzard.wire.egress import EgressBackfillFailure
from blizzard.wire.flags import FlagOffProblem
from blizzard.wire.graph import GraphValidationReport
from blizzard.wire.host import HostNameTakenProblem, HostRetiredProblem
from blizzard.wire.invariants import InvariantsInProgressProblem
from blizzard.wire.problem import (
    HubStoreUnavailableProblem,
)
from blizzard.wire.project import (
    ProjectRequiredProblem,
    UnknownProjectProblem,
)
from blizzard.wire.question import QuestionAlreadyAnsweredProblem, QuestionNotFoundProblem
from blizzard.wire.release import ReleaseProblem
from blizzard.wire.route import (
    ClaimRunnerRetiredProblem,
    ClaimRunnerUnregisteredProblem,
    ClaimRunnerVersionRefusedProblem,
    MalformedCursorProblem,
    PinnedGraphMissingProblem,
    RouteClaimConflictProblem,
    RouteClaimDependencyProblem,
    RouteClaimIncompatibleProblem,
    RouteClaimNotReadyProblem,
    RouteClaimPausedProblem,
    RouteClaimTerminalProblem,
    RouteNotFoundProblem,
    RouteNotHeldProblem,
    RouteTerminalProblem,
)
from blizzard.wire.runner import (
    RunnerAlreadyHostedProblem,
    RunnerHoldsRoutesProblem,
    RunnerIdentityProblem,
    RunnerLifecycleConflictProblem,
    RunnerNotFoundProblem,
    RunnerNotRegisteredProblem,
    RunnerOwnershipProblem,
    RunnerRetiredProblem,
    RunnerRetireInterruptedProblem,
    RunnerVersionProblem,
    UnknownServedProjectProblem,
)
from blizzard.wire.secret import SecretAlreadyExistsProblem, SecretNotFoundProblem
from blizzard.wire.system_artifact import SystemArtifactNotFoundProblem
from blizzard.wire.tenant import (
    TenantCredentialMismatchProblem,
    TenantNotFoundProblem,
    TenantRequiredProblem,
)
from blizzard.wire.traces import TraceReplayFailure
from blizzard.wire.transcript_segment import LeaseOwnershipProblem


@dto
@dataclass(frozen=True)
class ProblemDeclaration:
    """One operation/status/model variant, independent of route registration."""

    method: str
    path: str
    status_code: int
    model: type[BaseModel]


# Resource refusals at the fleet proxy boundary.
PROXY_RESOURCE_DECLARATIONS: tuple[ProblemDeclaration, ...] = (
    ProblemDeclaration("GET", "/api/fleet/queue/peek", 422, UnknownProjectProblem),
    ProblemDeclaration("GET", "/api/auth/authorize", 403, RunnerRetiredProblem),
    ProblemDeclaration("GET", "/api/auth/authorize", 403, HostRetiredProblem),
    ProblemDeclaration("GET", "/api/fleet/scopes", 404, ChunkNotFoundProblem),
    ProblemDeclaration("GET", "/api/fleet/system-artifacts/{name:path}", 404, SystemArtifactNotFoundProblem),
    ProblemDeclaration("GET", "/api/fleet/chunks/{chunk_id}/envelope", 404, ChunkNotFoundProblem),
    ProblemDeclaration("GET", "/api/fleet/chunks/{chunk_id}/envelope", 409, NoCurrentNodeProblem),
    ProblemDeclaration("GET", "/api/fleet/chunks/{chunk_id}/envelope", 500, PinnedGraphMissingProblem),
    ProblemDeclaration("POST", "/api/fleet/chunks/{chunk_id}/artifact-check", 500, PinnedGraphMissingProblem),
    ProblemDeclaration("POST", "/api/fleet/chunks/{chunk_id}/hub-advance", 500, PinnedGraphMissingProblem),
    ProblemDeclaration("POST", "/api/fleet/routes", 500, PinnedGraphMissingProblem),
    ProblemDeclaration("POST", "/api/fleet/chunks/{chunk_id}/completions", 500, PinnedGraphMissingProblem),
    ProblemDeclaration("POST", "/api/fleet/chunks/{chunk_id}/decisions", 500, PinnedGraphMissingProblem),
    ProblemDeclaration("GET", "/api/fleet/identity", 401, RunnerIdentityProblem),
    ProblemDeclaration("POST", "/api/fleet/host/runners", 403, HostRetiredProblem),
    ProblemDeclaration("POST", "/api/fleet/host/runners", 422, UnknownServedProjectProblem),
    ProblemDeclaration("POST", "/api/fleet/host/runners/{runner_id}/retire", 404, RunnerNotFoundProblem),
    ProblemDeclaration("POST", "/api/fleet/host/runners/{runner_id}/retire", 409, RunnerHoldsRoutesProblem),
    ProblemDeclaration("POST", "/api/fleet/host/runners/{runner_id}/retire", 409, RunnerLifecycleConflictProblem),
    ProblemDeclaration("POST", "/api/fleet/host/runners/{runner_id}/retire", 409, RunnerRetireInterruptedProblem),
    ProblemDeclaration("POST", "/api/fleet/routes", 404, ChunkNotFoundProblem),
    *(
        ProblemDeclaration("POST", "/api/fleet/routes", 403, model)
        for model in (
            RouteClaimPausedProblem,
            ClaimRunnerRetiredProblem,
            ClaimRunnerUnregisteredProblem,
            ClaimRunnerVersionRefusedProblem,
        )
    ),
    *(
        ProblemDeclaration("POST", "/api/fleet/routes", 409, model)
        for model in (
            RouteClaimTerminalProblem,
            RouteClaimNotReadyProblem,
            RouteClaimDependencyProblem,
            RouteClaimIncompatibleProblem,
            RouteClaimConflictProblem,
        )
    ),
    ProblemDeclaration("POST", "/api/fleet/runners", 404, RunnerNotFoundProblem),
    ProblemDeclaration("POST", "/api/fleet/runners", 409, RunnerVersionProblem),
    ProblemDeclaration("GET", "/api/fleet/runners/{runner_id}", 403, RunnerOwnershipProblem),
    ProblemDeclaration("GET", "/api/fleet/runners/{runner_id}", 404, RunnerNotFoundProblem),
    ProblemDeclaration("GET", "/api/fleet/runners/{runner_id}", 409, RunnerNotRegisteredProblem),
    ProblemDeclaration("GET", "/api/me", 401, AuthenticationRequiredProblem),
    ProblemDeclaration("POST", "/api/hosts", 404, RunnerNotFoundProblem),
    *(
        ProblemDeclaration("POST", "/api/hosts", 409, model)
        for model in (
            HostNameTakenProblem,
            RunnerAlreadyHostedProblem,
            RunnerRetiredProblem,
            RunnerLifecycleConflictProblem,
        )
    ),
    ProblemDeclaration("GET", "/api/fleet/questions/{question_id}", 404, QuestionNotFoundProblem),
    ProblemDeclaration("POST", "/api/fleet/chunks/{chunk_id}/pause", 409, ChunkNotPausableProblem),
    ProblemDeclaration("GET", "/api/fleet/chunks/{chunk_id}/transcript-segments", 403, LeaseOwnershipProblem),
    ProblemDeclaration("POST", "/api/fleet/chunks/{chunk_id}/release", 403, RouteNotHeldProblem),
    ProblemDeclaration("POST", "/api/fleet/chunks/{chunk_id}/release", 409, ReleaseProblem),
    ProblemDeclaration("POST", "/api/fleet/chunks/{chunk_id}/route-token", 403, RouteNotHeldProblem),
    ProblemDeclaration("POST", "/api/fleet/chunks/{chunk_id}/route-token", 404, RouteNotFoundProblem),
    ProblemDeclaration("POST", "/api/fleet/chunks/{chunk_id}/route-token", 409, RouteTerminalProblem),
    ProblemDeclaration("GET", "/api/fleet/runners/{runner_id}/decisions", 403, RunnerOwnershipProblem),
    ProblemDeclaration("POST", "/api/fleet/runners/{runner_id}/heartbeats", 403, RunnerOwnershipProblem),
    ProblemDeclaration("POST", "/api/fleet/runners/{runner_id}/heartbeats", 404, RunnerNotFoundProblem),
    *(
        ProblemDeclaration("GET", path, 404, ChunkNotFoundProblem)
        for path in (
            "/api/fleet/chunks/{chunk_id}",
            "/api/fleet/chunks/{chunk_id}/work-items",
            "/api/fleet/chunks/{chunk_id}/pm-items",
            "/api/fleet/chunks/{chunk_id}/findings",
            "/api/fleet/chunks/{chunk_id}/findings/{finding_id}",
        )
    ),
    *(
        ProblemDeclaration("POST", f"/api/fleet/chunks/{{chunk_id}}/{verb}", 404, ChunkNotFoundProblem)
        for verb in ("pause", "resume", "release", "hub-advance", "completions", "decisions", "artifact-check")
    ),
    *(
        ProblemDeclaration("GET", path, 404, model)
        for path in (
            "/api/fleet/chunks/{chunk_id}/findings",
            "/api/fleet/chunks/{chunk_id}/findings/{finding_id}",
        )
        for model in (NoAnsweredProposalProblem,)
    ),
    ProblemDeclaration("GET", "/api/fleet/chunks/{chunk_id}/findings/{finding_id}", 404, FindingNotInChunkProblem),
    ProblemDeclaration("GET", "/api/fleet/chunks/{chunk_id}/garden/proposals/page", 422, MalformedCursorProblem),
)


# The worker's garden and analytics reads share _routine_run_or_404.
_ROUTINE_READS = (
    "/api/fleet/chunks/{chunk_id}/garden/findings",
    "/api/fleet/chunks/{chunk_id}/garden/proposals",
    "/api/fleet/chunks/{chunk_id}/garden/proposals/page",
    "/api/fleet/chunks/{chunk_id}/analytics/counts/files",
    "/api/fleet/chunks/{chunk_id}/analytics/counts/skills",
    "/api/fleet/chunks/{chunk_id}/analytics/counts/agent-types",
    "/api/fleet/chunks/{chunk_id}/analytics/counts/nodes",
    "/api/fleet/chunks/{chunk_id}/analytics/spend/nodes",
    "/api/fleet/chunks/{chunk_id}/analytics/spend/graphs",
)
ROUTINE_RESOURCE_DECLARATIONS = tuple(
    ProblemDeclaration("GET", path, 404, model)
    for path in _ROUTINE_READS
    for model in (ChunkNotFoundProblem, NoRunContextProblem)
)


# Runner-bound operations the runner clients and HubProxy call.
_RUNNER_OPERATIONS = (
    *(("GET", path) for path in _ROUTINE_READS),
    *(
        ("GET", path)
        for path in (
            "/api/fleet/chunk-statuses",
            "/api/fleet/chunks/{chunk_id}",
            "/api/fleet/chunks/{chunk_id}/envelope",
            "/api/fleet/chunks/{chunk_id}/findings",
            "/api/fleet/chunks/{chunk_id}/findings/{finding_id}",
            "/api/fleet/chunks/{chunk_id}/transcript-segments",
            "/api/fleet/chunks/{chunk_id}/work-items",
            "/api/fleet/chunks/{chunk_id}/pm-items",
            "/api/fleet/questions/{question_id}",
            "/api/fleet/queue/peek",
            "/api/fleet/runners/{runner_id}",
            "/api/fleet/runners/{runner_id}/decisions",
            "/api/fleet/scopes",
            "/api/fleet/summary",
            "/api/fleet/system-artifacts",
            "/api/fleet/system-artifacts/{name:path}",
        )
    ),
    *(
        ("POST", f"/api/fleet/chunks/{{chunk_id}}/{verb}")
        for verb in (
            "artifact-check",
            "completions",
            "decisions",
            "hub-advance",
            "pause",
            "release",
            "resume",
            "route-token",
        )
    ),
    *(
        ("POST", path)
        for path in (
            "/api/fleet/events",
            "/api/fleet/queue/peek",
            "/api/fleet/routes",
            "/api/fleet/runners",
            "/api/fleet/runners/{runner_id}/heartbeats",
            "/api/fleet/transcripts",
        )
    ),
)
_HOST_OPERATIONS = (
    ("GET", "/api/fleet/host"),
    ("POST", "/api/fleet/host"),
    ("POST", "/api/fleet/host/events"),
    ("POST", "/api/fleet/host/runners"),
    ("POST", "/api/fleet/host/runners/{runner_id}/retire"),
)
FLEET_DEPENDENCY_DECLARATIONS = (
    *(
        ProblemDeclaration(method, path, 401, model)
        for method, path in (*_RUNNER_OPERATIONS, *_HOST_OPERATIONS)
        for model in (AuthenticationRequiredProblem, AuthenticationRevokedProblem, AuthenticationUnresolvedProblem)
    ),
    *(
        ProblemDeclaration(method, path, 400, TenantCredentialMismatchProblem)
        for method, path in (*_RUNNER_OPERATIONS, *_HOST_OPERATIONS)
    ),
    *(
        ProblemDeclaration(method, path, 404, TenantNotFoundProblem)
        for method, path in (*_RUNNER_OPERATIONS, *_HOST_OPERATIONS)
    ),
    *(
        ProblemDeclaration(method, path, 403, RunnerRetiredProblem)
        for method, path in _RUNNER_OPERATIONS
        if path != "/api/fleet/routes"
    ),
    *(ProblemDeclaration(method, path, 403, HostRetiredProblem) for method, path in _HOST_OPERATIONS),
)

# POST /hosts resolves a person and tenant and then demands RUNNER_RETIRE.
BOOTSTRAP_DEPENDENCY_DECLARATIONS = (
    ProblemDeclaration("POST", "/api/hosts", 400, TenantCredentialMismatchProblem),
    ProblemDeclaration("POST", "/api/hosts", 401, AuthenticationRequiredProblem),
    ProblemDeclaration("POST", "/api/hosts", 403, MachineCredentialForbiddenProblem),
    ProblemDeclaration("POST", "/api/hosts", 403, RunnerCredentialForbiddenProblem),
    ProblemDeclaration("POST", "/api/hosts", 403, PermissionRequiredProblem),
    ProblemDeclaration("POST", "/api/hosts", 404, TenantNotFoundProblem),
    ProblemDeclaration("POST", "/api/hosts", 409, TenantRequiredProblem),
)

# The operations the operator CLI and the board call, by the shared dependency each reaches.
_OPERATOR_TENANT_OPERATIONS = (
    ("GET", "/api/activity"),
    ("GET", "/api/analytics/counts/agent-types"),
    ("GET", "/api/analytics/counts/files"),
    ("GET", "/api/analytics/counts/nodes"),
    ("GET", "/api/analytics/counts/skills"),
    ("GET", "/api/analytics/durations/graphs"),
    ("GET", "/api/analytics/durations/nodes"),
    ("GET", "/api/analytics/events"),
    ("GET", "/api/analytics/events/ndjson"),
    ("GET", "/api/analytics/outcomes/nodes"),
    ("POST", "/api/analytics/re-derive"),
    ("GET", "/api/analytics/spend/chunks"),
    ("GET", "/api/analytics/spend/chunks/ndjson"),
    ("GET", "/api/analytics/spend/graphs"),
    ("GET", "/api/analytics/spend/models"),
    ("GET", "/api/analytics/spend/nodes"),
    ("GET", "/api/backlog"),
    ("PUT", "/api/backlog"),
    ("POST", "/api/backlog/position"),
    ("GET", "/api/cadences"),
    ("GET", "/api/cadences/{cadence_id}"),
    ("PATCH", "/api/cadences/{cadence_id}"),
    ("POST", "/api/cadences/{cadence_id}/enable"),
    ("GET", "/api/cadences/{cadence_id}/errands"),
    ("POST", "/api/cadences/{cadence_id}/retire"),
    ("GET", "/api/chunk-counts"),
    ("GET", "/api/chunks"),
    ("POST", "/api/chunks"),
    ("DELETE", "/api/chunks/{chunk_id}"),
    ("GET", "/api/chunks/{chunk_id}"),
    ("PATCH", "/api/chunks/{chunk_id}"),
    ("GET", "/api/chunks/{chunk_id}/close-diagnostic"),
    ("POST", "/api/chunks/{chunk_id}/complete"),
    ("POST", "/api/chunks/{chunk_id}/demote"),
    ("POST", "/api/chunks/{chunk_id}/dependencies"),
    ("POST", "/api/chunks/{chunk_id}/dependencies/release"),
    ("POST", "/api/chunks/{chunk_id}/detach"),
    ("GET", "/api/chunks/{chunk_id}/flow-submissions"),
    ("GET", "/api/chunks/{chunk_id}/flow-submissions/{submission_id}/turns"),
    ("POST", "/api/chunks/{chunk_id}/group"),
    ("GET", "/api/chunks/{chunk_id}/landing-facts"),
    ("POST", "/api/chunks/{chunk_id}/pause"),
    ("GET", "/api/chunks/{chunk_id}/pm-items"),
    ("POST", "/api/chunks/{chunk_id}/promote"),
    ("POST", "/api/chunks/{chunk_id}/requeues"),
    ("POST", "/api/chunks/{chunk_id}/restart"),
    ("POST", "/api/chunks/{chunk_id}/resume"),
    ("POST", "/api/chunks/{chunk_id}/stop"),
    ("GET", "/api/chunks/{chunk_id}/transcripts"),
    ("GET", "/api/chunks/{chunk_id}/transcripts/{segment_id}"),
    ("GET", "/api/chunks/{chunk_id}/work-items"),
    ("POST", "/api/config/apply"),
    ("GET", "/api/config/changes"),
    ("GET", "/api/config/export"),
    ("GET", "/api/config/schema/{kind}"),
    ("GET", "/api/decisions"),
    ("POST", "/api/decisions/{decision_id}/resolutions"),
    ("GET", "/api/events"),
    ("GET", "/api/findings"),
    ("GET", "/api/findings/classes"),
    ("POST", "/api/findings/confirm-gone"),
    ("POST", "/api/findings/not-a-finding"),
    ("POST", "/api/findings/reopen"),
    ("POST", "/api/findings/resolve"),
    ("POST", "/api/findings/supersede"),
    ("POST", "/api/findings/wont-fix"),
    ("GET", "/api/findings/{finding_id}"),
    ("GET", "/api/flows"),
    ("GET", "/api/flows/{flow_id}"),
    ("GET", "/api/flows/{flow_id}/record"),
    ("GET", "/api/forges"),
    ("POST", "/api/forges"),
    ("GET", "/api/forges/{forge}"),
    ("PATCH", "/api/forges/{forge}"),
    ("POST", "/api/forges/{forge}/enable"),
    ("POST", "/api/forges/{forge}/retire"),
    ("GET", "/api/garden-proposals"),
    ("POST", "/api/garden-proposals"),
    ("GET", "/api/garden-proposals/{proposal_id}"),
    ("PATCH", "/api/garden-proposals/{proposal_id}"),
    ("POST", "/api/garden-proposals/{proposal_id}/accept"),
    ("POST", "/api/garden-proposals/{proposal_id}/attach"),
    ("POST", "/api/garden-proposals/{proposal_id}/detach"),
    ("POST", "/api/garden-proposals/{proposal_id}/pass"),
    ("GET", "/api/graphs"),
    ("POST", "/api/graphs"),
    ("POST", "/api/graphs/sync"),
    ("GET", "/api/graphs/{graph_id}"),
    ("PATCH", "/api/graphs/{graph_id}"),
    ("POST", "/api/graphs/{graph_id}/enable"),
    ("POST", "/api/graphs/{graph_id}/follow-latest"),
    ("POST", "/api/graphs/{graph_id}/retire"),
    ("GET", "/api/hosts"),
    ("POST", "/api/hosts"),
    ("GET", "/api/hosts/{host_id}"),
    ("POST", "/api/hosts/{host_id}/adoptions"),
    ("POST", "/api/hosts/{host_id}/enrollments"),
    ("POST", "/api/hosts/{host_id}/releases"),
    ("POST", "/api/hosts/{host_id}/retire"),
    ("POST", "/api/hosts/{host_id}/token-revocations"),
    ("GET", "/api/invitations"),
    ("POST", "/api/invitations"),
    ("POST", "/api/invitations/{invitation_id}/revocations"),
    ("GET", "/api/members"),
    ("DELETE", "/api/members/{user_id}"),
    ("PUT", "/api/members/{user_id}"),
    ("GET", "/api/projects"),
    ("POST", "/api/projects"),
    ("GET", "/api/projects/{project}"),
    ("PATCH", "/api/projects/{project}"),
    ("GET", "/api/projects/{project}/cadences"),
    ("POST", "/api/projects/{project}/cadences"),
    ("GET", "/api/projects/{project}/findings"),
    ("GET", "/api/projects/{project}/findings/classes"),
    ("GET", "/api/projects/{project}/garden-proposals"),
    ("POST", "/api/projects/{project}/garden-proposals"),
    ("PUT", "/api/projects/{project}/landing-order"),
    ("POST", "/api/projects/{project}/landing-order/position"),
    ("GET", "/api/projects/{project}/repository-links"),
    ("PUT", "/api/projects/{project}/repository-links/{repository}"),
    ("POST", "/api/projects/{project}/repository-links/{repository}/retire"),
    ("GET", "/api/projects/{project}/routines"),
    ("POST", "/api/projects/{project}/routines"),
    ("GET", "/api/projects/{project}/runs"),
    ("GET", "/api/projects/{project}/runs/{chunk_id}"),
    ("GET", "/api/projects/{project}/scopes"),
    ("POST", "/api/projects/{project}/scopes"),
    ("GET", "/api/projects/{project}/scopes/{slug}"),
    ("PATCH", "/api/projects/{project}/scopes/{slug}"),
    ("POST", "/api/projects/{project}/scopes/{slug}/enable"),
    ("POST", "/api/projects/{project}/scopes/{slug}/retire"),
    ("GET", "/api/projects/{project}/scopes/{slug}/routines"),
    ("GET", "/api/projects/{project}/source-links"),
    ("PUT", "/api/projects/{project}/source-links/{source}"),
    ("POST", "/api/projects/{project}/source-links/{source}/retire"),
    ("GET", "/api/questions"),
    ("POST", "/api/questions"),
    ("POST", "/api/questions/{question_id}/answers"),
    ("GET", "/api/queue"),
    ("PUT", "/api/queue"),
    ("POST", "/api/queue/position"),
    ("GET", "/api/repositories"),
    ("POST", "/api/repositories"),
    ("GET", "/api/repositories/{repository}"),
    ("PATCH", "/api/repositories/{repository}"),
    ("POST", "/api/repositories/{repository}/enable"),
    ("POST", "/api/repositories/{repository}/retire"),
    ("GET", "/api/routines"),
    ("POST", "/api/routines"),
    ("GET", "/api/routines/proposal-counts"),
    ("GET", "/api/routines/trend"),
    ("GET", "/api/routines/{routine_id}"),
    ("PATCH", "/api/routines/{routine_id}"),
    ("GET", "/api/routines/{routine_id}/baselines"),
    ("POST", "/api/routines/{routine_id}/enable"),
    ("POST", "/api/routines/{routine_id}/retire"),
    ("POST", "/api/routines/{routine_id}/run"),
    ("GET", "/api/routines/{routine_id}/scopes"),
    ("DELETE", "/api/routines/{routine_id}/scopes/{scope_slug}"),
    ("PUT", "/api/routines/{routine_id}/scopes/{scope_slug}"),
    ("GET", "/api/routines/{routine_id}/sweeps"),
    ("GET", "/api/runners"),
    ("POST", "/api/runners"),
    ("GET", "/api/runners/{runner_id}"),
    ("POST", "/api/runners/{runner_id}/enrollments"),
    ("POST", "/api/runners/{runner_id}/pause"),
    ("POST", "/api/runners/{runner_id}/reinstate"),
    ("POST", "/api/runners/{runner_id}/resume"),
    ("POST", "/api/runners/{runner_id}/retire"),
    ("POST", "/api/runners/{runner_id}/token-revocations"),
    ("GET", "/api/runs"),
    ("GET", "/api/runs/{chunk_id}"),
    ("GET", "/api/scopes"),
    ("POST", "/api/scopes"),
    ("GET", "/api/scopes/{slug}"),
    ("PATCH", "/api/scopes/{slug}"),
    ("POST", "/api/scopes/{slug}/enable"),
    ("POST", "/api/scopes/{slug}/retire"),
    ("GET", "/api/scopes/{slug}/routines"),
    ("GET", "/api/secrets"),
    ("POST", "/api/secrets"),
    ("GET", "/api/secrets/{name}"),
    ("POST", "/api/secrets/{name}/enable"),
    ("POST", "/api/secrets/{name}/retire"),
    ("PUT", "/api/secrets/{name}/value"),
    ("GET", "/api/spend"),
    ("POST", "/api/sweeps/{name}/passes"),
    ("GET", "/api/users"),
    ("POST", "/api/users/{user_id}/role"),
    ("GET", "/api/work-items"),
    ("GET", "/api/work-sources"),
    ("POST", "/api/work-sources"),
    ("GET", "/api/work-sources/{source}"),
    ("PATCH", "/api/work-sources/{source}"),
    ("POST", "/api/work-sources/{source}/enable"),
    ("GET", "/api/work-sources/{source}/items"),
    ("POST", "/api/work-sources/{source}/items"),
    ("DELETE", "/api/work-sources/{source}/items/{ref}"),
    ("GET", "/api/work-sources/{source}/items/{ref}"),
    ("PATCH", "/api/work-sources/{source}/items/{ref}"),
    ("POST", "/api/work-sources/{source}/retire"),
)
# Hub-level operations, which resolve a person and no tenant.
_OPERATOR_HUB_OPERATIONS = (
    ("GET", "/api/admin/events"),
    ("POST", "/api/admin/graphs/sync"),
    ("GET", "/api/admin/hub-admins"),
    ("DELETE", "/api/admin/hub-admins/{user_id}"),
    ("PUT", "/api/admin/hub-admins/{user_id}"),
    ("GET", "/api/admin/invariants"),
    ("POST", "/api/admin/sweeps/{name}/passes"),
    ("GET", "/api/admin/tenants"),
    ("POST", "/api/admin/tenants"),
    ("DELETE", "/api/admin/tenants/{tenant_id}"),
    ("PATCH", "/api/admin/tenants/{tenant_id}"),
    ("POST", "/api/egress/backfill"),
    ("POST", "/api/egress/reset"),
    ("GET", "/api/egress/status"),
    ("POST", "/api/traces/replay"),
    ("GET", "/api/traces/status"),
)
# Reads gated on a permission every membership role holds, which no member is refused.
_OPERATOR_MEMBER_READS = (
    ("GET", "/api/activity"),
    ("GET", "/api/cadences"),
    ("GET", "/api/cadences/{cadence_id}"),
    ("GET", "/api/cadences/{cadence_id}/errands"),
    ("GET", "/api/chunk-counts"),
    ("GET", "/api/chunks"),
    ("GET", "/api/chunks/{chunk_id}"),
    ("GET", "/api/chunks/{chunk_id}/close-diagnostic"),
    ("GET", "/api/chunks/{chunk_id}/flow-submissions"),
    ("GET", "/api/chunks/{chunk_id}/flow-submissions/{submission_id}/turns"),
    ("GET", "/api/chunks/{chunk_id}/landing-facts"),
    ("GET", "/api/chunks/{chunk_id}/pm-items"),
    ("GET", "/api/chunks/{chunk_id}/work-items"),
    ("GET", "/api/config/changes"),
    ("GET", "/api/config/export"),
    ("GET", "/api/config/schema/{kind}"),
    ("GET", "/api/decisions"),
    ("GET", "/api/events"),
    ("GET", "/api/findings"),
    ("GET", "/api/findings/classes"),
    ("GET", "/api/findings/{finding_id}"),
    ("GET", "/api/flows"),
    ("GET", "/api/flows/{flow_id}"),
    ("GET", "/api/flows/{flow_id}/record"),
    ("GET", "/api/forges"),
    ("GET", "/api/forges/{forge}"),
    ("GET", "/api/garden-proposals"),
    ("GET", "/api/garden-proposals/{proposal_id}"),
    ("GET", "/api/graphs"),
    ("GET", "/api/graphs/{graph_id}"),
    ("GET", "/api/hosts"),
    ("GET", "/api/hosts/{host_id}"),
    ("GET", "/api/projects"),
    ("GET", "/api/projects/{project}"),
    ("GET", "/api/projects/{project}/cadences"),
    ("GET", "/api/projects/{project}/findings"),
    ("GET", "/api/projects/{project}/findings/classes"),
    ("GET", "/api/projects/{project}/garden-proposals"),
    ("GET", "/api/projects/{project}/repository-links"),
    ("GET", "/api/projects/{project}/routines"),
    ("GET", "/api/projects/{project}/runs"),
    ("GET", "/api/projects/{project}/runs/{chunk_id}"),
    ("GET", "/api/projects/{project}/scopes"),
    ("GET", "/api/projects/{project}/scopes/{slug}"),
    ("GET", "/api/projects/{project}/scopes/{slug}/routines"),
    ("GET", "/api/projects/{project}/source-links"),
    ("GET", "/api/questions"),
    ("GET", "/api/queue"),
    ("GET", "/api/repositories"),
    ("GET", "/api/repositories/{repository}"),
    ("GET", "/api/routines"),
    ("GET", "/api/routines/proposal-counts"),
    ("GET", "/api/routines/trend"),
    ("GET", "/api/routines/{routine_id}"),
    ("GET", "/api/routines/{routine_id}/baselines"),
    ("GET", "/api/routines/{routine_id}/scopes"),
    ("GET", "/api/routines/{routine_id}/sweeps"),
    ("GET", "/api/runners"),
    ("GET", "/api/runners/{runner_id}"),
    ("GET", "/api/runs"),
    ("GET", "/api/runs/{chunk_id}"),
    ("GET", "/api/scopes"),
    ("GET", "/api/scopes/{slug}"),
    ("GET", "/api/scopes/{slug}/routines"),
    ("GET", "/api/secrets"),
    ("GET", "/api/secrets/{name}"),
    ("GET", "/api/spend"),
    ("GET", "/api/work-items"),
    ("GET", "/api/work-sources"),
    ("GET", "/api/work-sources/{source}"),
    ("GET", "/api/work-sources/{source}/items"),
    ("GET", "/api/work-sources/{source}/items/{ref}"),
)
# Operations that act in the tenant's only project when the call names none.
_OPERATOR_PROJECT_OPERATIONS = (
    ("POST", "/api/chunks"),
    ("GET", "/api/findings"),
    ("GET", "/api/findings/classes"),
    ("POST", "/api/garden-proposals"),
    ("POST", "/api/routines"),
    ("GET", "/api/routines/proposal-counts"),
    ("GET", "/api/routines/trend"),
    ("POST", "/api/scopes"),
    ("GET", "/api/scopes/{slug}"),
    ("PATCH", "/api/scopes/{slug}"),
    ("POST", "/api/scopes/{slug}/enable"),
    ("POST", "/api/scopes/{slug}/retire"),
    ("GET", "/api/scopes/{slug}/routines"),
    ("POST", "/api/work-sources/{source}/items"),
)

_OPERATOR_OPERATIONS = (*_OPERATOR_TENANT_OPERATIONS, *_OPERATOR_HUB_OPERATIONS)
_OPERATOR_PERMISSION_OPERATIONS = tuple(
    operation for operation in _OPERATOR_OPERATIONS if operation not in _OPERATOR_MEMBER_READS
)
# Operations a hub flag holds (``flag_guard``).
_FLAG_GUARDED_OPERATIONS = tuple[tuple[str, str], ...]()
OPERATOR_DEPENDENCY_DECLARATIONS = (
    *(ProblemDeclaration(method, path, 401, AuthenticationRequiredProblem) for method, path in _OPERATOR_OPERATIONS),
    *(
        ProblemDeclaration(method, path, 403, PermissionRequiredProblem)
        for method, path in _OPERATOR_PERMISSION_OPERATIONS
    ),
    *(
        ProblemDeclaration(method, path, status, model)
        for method, path in _OPERATOR_TENANT_OPERATIONS
        for status, model in (
            (400, TenantCredentialMismatchProblem),
            (404, TenantNotFoundProblem),
            (409, TenantRequiredProblem),
        )
    ),
    *(ProblemDeclaration(method, path, 422, ProjectRequiredProblem) for method, path in _OPERATOR_PROJECT_OPERATIONS),
    *(ProblemDeclaration(method, path, 404, FlagOffProblem) for method, path in _FLAG_GUARDED_OPERATIONS),
    *(ProblemDeclaration(method, path, 503, HubStoreUnavailableProblem) for method, path in _OPERATOR_OPERATIONS),
)

HUB_DEPENDENCY_DECLARATIONS = tuple(
    ProblemDeclaration(method, path, 503, HubStoreUnavailableProblem)
    for method, path in (
        *_RUNNER_OPERATIONS,
        *_HOST_OPERATIONS,
        ("GET", "/api/fleet/identity"),
        ("GET", "/api/me"),
        ("POST", "/api/hosts"),
        ("GET", "/api/auth/authorize"),
        ("GET", "/api/auth/jwks.json"),
        ("POST", "/api/auth/cli/token"),
        ("GET", "/api/auth/{name}/authorize"),
        ("GET", "/api/auth/{name}/callback"),
    )
)

FEDERATION_DECLARATIONS = (
    *(
        ProblemDeclaration("GET", path, 404, IdpDisabledProblem)
        for path in ("/api/auth/authorize", "/api/auth/jwks.json")
    ),
    *(
        ProblemDeclaration("GET", "/api/auth/authorize", 400, model)
        for model in (
            FederationResponseModeProblem,
            FederationClientRefusedProblem,
            CliRedirectRefusedProblem,
            CliPkceRequiredProblem,
        )
    ),
    ProblemDeclaration("GET", "/api/auth/authorize", 501, FederationNoProviderProblem),
    ProblemDeclaration("POST", "/api/auth/cli/token", 404, IdpDisabledProblem),
    ProblemDeclaration("POST", "/api/auth/cli/token", 400, CliTokenRefusedProblem),
    *(
        ProblemDeclaration("GET", f"/api/auth/{{name}}/{action}", 404, LoginDisabledProblem)
        for action in ("authorize", "callback")
    ),
    *(
        ProblemDeclaration("GET", f"/api/auth/{{name}}/{action}", 429, LoginThrottledProblem)
        for action in ("authorize", "callback")
    ),
    ProblemDeclaration("GET", "/api/auth/{name}/authorize", 404, LoginProviderUnknownProblem),
    ProblemDeclaration("GET", "/api/auth/{name}/callback", 400, LoginFailedProblem),
    ProblemDeclaration("GET", "/api/auth/{name}/callback", 400, SsoRefusedProblem),
)

# The hub administrator's invariant check, which the operator CLI calls.
ADMIN_DECLARATIONS = (ProblemDeclaration("GET", "/api/admin/invariants", 409, InvariantsInProgressProblem),)

# Operator refusals the hub CLI tells apart by type.
OPERATOR_DECLARATIONS = (
    ProblemDeclaration("POST", "/api/secrets", 409, SecretAlreadyExistsProblem),
    *(
        ProblemDeclaration(method, path, 404, SecretNotFoundProblem)
        for method, path in (
            ("PUT", "/api/secrets/{name}/value"),
            ("GET", "/api/secrets/{name}"),
            ("POST", "/api/secrets/{name}/retire"),
            ("POST", "/api/secrets/{name}/enable"),
        )
    ),
    ProblemDeclaration("POST", "/api/decisions/{decision_id}/resolutions", 409, DecisionResolutionConflict),
    ProblemDeclaration("POST", "/api/questions/{question_id}/answers", 409, QuestionAlreadyAnsweredProblem),
    ProblemDeclaration("POST", "/api/egress/backfill", 502, EgressBackfillFailure),
    ProblemDeclaration("POST", "/api/traces/replay", 502, TraceReplayFailure),
    *(
        ProblemDeclaration(method, f"/api/cadences/{{cadence_id}}{suffix}", 409, RevisionConflictProblem)
        for method, suffix in (("PATCH", ""), ("POST", "/retire"), ("POST", "/enable"))
    ),
    *(
        ProblemDeclaration("POST", path, 409, ChunkIngestConflict)
        for path in (
            "/api/chunks",
            "/api/work-sources/{source}/items",
            "/api/routines/{routine_id}/run",
            "/api/garden-proposals/{proposal_id}/accept",
        )
    ),
    *(
        ProblemDeclaration("POST", "/api/chunks/{chunk_id}/dependencies", 409, model)
        for model in (DependentNotEditableView, DependencyWouldCloseCycleView, PrerequisiteIsEphemeralView)
    ),
    ProblemDeclaration("POST", "/api/chunks/{chunk_id}/dependencies/release", 409, NoStandingDependencyView),
    ProblemDeclaration("POST", "/api/config/apply", 409, ConfigApplyConflictProblem),
    ProblemDeclaration("POST", "/api/config/apply", 415, ConfigMediaTypeUnsupportedProblem),
    *(
        ProblemDeclaration(method, path, 422, ConfigDocumentInvalidProblem)
        for method, path in (
            ("POST", "/api/config/apply"),
            ("GET", "/api/config/export"),
            ("GET", "/api/config/schema/{kind}"),
        )
    ),
    ProblemDeclaration("POST", "/api/graphs", 422, GraphValidationReport),
)

HUB_PROBLEM_DECLARATIONS = (
    *PROXY_RESOURCE_DECLARATIONS,
    *ROUTINE_RESOURCE_DECLARATIONS,
    *FLEET_DEPENDENCY_DECLARATIONS,
    *BOOTSTRAP_DEPENDENCY_DECLARATIONS,
    *OPERATOR_DEPENDENCY_DECLARATIONS,
    *HUB_DEPENDENCY_DECLARATIONS,
    *FEDERATION_DECLARATIONS,
    *ADMIN_DECLARATIONS,
    *OPERATOR_DECLARATIONS,
)


def problem_declarations_by_status(
    declarations: Sequence[ProblemDeclaration], *, method: str, path: str
) -> dict[int, tuple[type[BaseModel], ...]]:
    """Select the distinct declared variants for an operation, in declaration order."""
    selected: dict[int, list[type[BaseModel]]] = {}
    for declaration in declarations:
        if declaration.method.upper() != method.upper() or declaration.path != path:
            continue
        models = selected.setdefault(declaration.status_code, [])
        if declaration.model not in models:
            models.append(declaration.model)
    return {status: tuple(models) for status, models in selected.items()}


def declared_problem_variants(request: Request) -> dict[int, tuple[type[BaseModel], ...]]:
    """The problem variants the request's matched operation declares, by status."""
    route = request.scope.get("route")
    return problem_declarations_by_status(
        HUB_PROBLEM_DECLARATIONS, method=request.method, path=getattr(route, "path", "")
    )

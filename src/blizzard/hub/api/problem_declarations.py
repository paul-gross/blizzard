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
from blizzard.wire.chunk import (
    ChunkNotFoundProblem,
    ChunkNotPausableProblem,
    FindingNotInChunkProblem,
    NoAnsweredProposalProblem,
    NoCurrentNodeProblem,
    NoRunContextProblem,
)
from blizzard.wire.host import HostNameTakenProblem, HostRetiredProblem
from blizzard.wire.problem import (
    HubStoreUnavailableProblem,
)
from blizzard.wire.project import (
    UnknownProjectProblem,
)
from blizzard.wire.question import QuestionNotFoundProblem
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
from blizzard.wire.system_artifact import SystemArtifactNotFoundProblem
from blizzard.wire.tenant import (
    TenantCredentialMismatchProblem,
    TenantNotFoundProblem,
    TenantRequiredProblem,
)
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

HUB_PROBLEM_DECLARATIONS = (
    *PROXY_RESOURCE_DECLARATIONS,
    *ROUTINE_RESOURCE_DECLARATIONS,
    *FLEET_DEPENDENCY_DECLARATIONS,
    *BOOTSTRAP_DEPENDENCY_DECLARATIONS,
    *HUB_DEPENDENCY_DECLARATIONS,
    *FEDERATION_DECLARATIONS,
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

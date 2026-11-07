"""Factories for hub-authored problem models.

Callers supply the already-sanitized instance and detail.
"""

from __future__ import annotations

from blizzard.foundation.claim_denials import ClaimDenialReason
from blizzard.foundation.release_refusals import ReleaseRefusalReason
from blizzard.hub.auth.permissions import Permission
from blizzard.hub.domain.chunk.errors import ChunkNotFound
from blizzard.hub.domain.execution.claim import (
    ClaimConflict,
    ClaimDeniedDependency,
    ClaimDeniedIncompatible,
    ClaimDeniedNotReady,
    ClaimDeniedPaused,
    ClaimDeniedTerminal,
    ClaimDeniedUnregistered,
    ClaimDeniedVersionRefused,
    RekeyDeniedNotHolder,
    RekeyDeniedTerminal,
)
from blizzard.hub.domain.execution.envelope import NoCurrentNode
from blizzard.hub.domain.execution.fleet import RetireInterrupted
from blizzard.hub.domain.execution.release import ReleaseRefusalKind, ReleaseRefused
from blizzard.hub.domain.kernel.pagination import MalformedCursor
from blizzard.hub.domain.observability.transcripts import LeaseSegmentsNotOwned
from blizzard.hub.domain.operations.pause import ChunkNotPausable
from blizzard.hub.domain.projects.model import ProjectRequired
from blizzard.hub.domain.runners.host import HostNameTaken, HostRetired, RunnerAlreadyHosted
from blizzard.hub.domain.runners.registration import (
    RunnerHoldsRoutes,
    RunnerLifecycleConflict,
    RunnerNeverConnected,
    RunnerRetired,
    RunnerTokenRefused,
)
from blizzard.hub.domain.runners.served import UnknownServedProject
from blizzard.hub.domain.runners.version import RunnerVersionRefused
from blizzard.wire import problem as problem_types
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
from blizzard.wire.invariants import InvariantsInProgressProblem
from blizzard.wire.problem import (
    CLAIM_CONFLICT,
    CLAIM_DEPENDENCY,
    CLAIM_INCOMPATIBLE,
    CLAIM_NOT_READY,
    CLAIM_PAUSED,
    CLAIM_RUNNER_RETIRED,
    CLAIM_RUNNER_UNREGISTERED,
    CLAIM_RUNNER_VERSION_REFUSED,
    CLAIM_TERMINAL,
    INVARIANTS_IN_PROGRESS,
    MALFORMED_CURSOR,
    RELEASE_REFUSAL,
    ROUTE_NOT_FOUND,
    ROUTE_NOT_HELD,
    ROUTE_TERMINAL,
    RUNNER_IDENTITY,
    RUNNER_VERSION,
    HubStoreUnavailableProblem,
)
from blizzard.wire.project import (
    ProjectRef,
    ProjectRequiredBody,
    ProjectRequiredProblem,
    UnknownProjectDetail,
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
    NO_SUCH_TENANT,
    TenantCredentialMismatchProblem,
    TenantNotFoundProblem,
    TenantRequiredDetail,
    TenantRequiredProblem,
)
from blizzard.wire.transcript_segment import LeaseOwnershipProblem


def unknown_query_project_problem(value: str, *, instance: str) -> UnknownProjectProblem:
    return UnknownProjectProblem(
        type=problem_types.UNKNOWN_PROJECT,
        title="Unknown project",
        detail=[
            UnknownProjectDetail(loc=["query", "project"], msg=f"unknown project {value!r}", type="unknown_project")
        ],
        instance=instance,
        status=422,
    )


def hub_store_unavailable_problem(*, instance: str) -> HubStoreUnavailableProblem:
    return HubStoreUnavailableProblem(
        type=problem_types.HUB_STORE_UNAVAILABLE,
        title="Hub store unavailable",
        detail="hub store not wired — start via `blizzard hub host`",
        instance=instance,
        status=503,
    )


def runner_credential_forbidden_problem(runner_id: str, *, instance: str) -> RunnerCredentialForbiddenProblem:
    return RunnerCredentialForbiddenProblem(
        type=problem_types.RUNNER_CREDENTIAL_FORBIDDEN,
        title="Runner credential forbidden",
        detail=f"runner token for {runner_id!r} is not valid on an operator verb",
        instance=instance,
        status=403,
    )


def idp_disabled_problem(*, instance: str) -> IdpDisabledProblem:
    return IdpDisabledProblem(
        type=problem_types.IDP_DISABLED,
        title="IdP disabled",
        detail="the IdP surface is not enabled",
        instance=instance,
        status=404,
    )


def federation_response_mode_problem(response_mode: str, *, instance: str) -> FederationResponseModeProblem:
    return FederationResponseModeProblem(
        type=problem_types.FEDERATION_RESPONSE_MODE,
        title="Unsupported response mode",
        detail=f"unknown response_mode {response_mode!r}",
        instance=instance,
        status=400,
    )


def federation_client_refused_problem(*, instance: str) -> FederationClientRefusedProblem:
    return FederationClientRefusedProblem(
        type=problem_types.FEDERATION_CLIENT_REFUSED,
        title="Federation client refused",
        detail="unknown client or unregistered redirect_uri",
        instance=instance,
        status=400,
    )


def federation_no_provider_problem(*, instance: str) -> FederationNoProviderProblem:
    return FederationNoProviderProblem(
        type=problem_types.FEDERATION_NO_PROVIDER,
        title="No authentication provider",
        detail="no hub session, and no configured providers to authenticate against",
        instance=instance,
        status=501,
    )


def cli_redirect_refused_problem(*, instance: str) -> CliRedirectRefusedProblem:
    return CliRedirectRefusedProblem(
        type=problem_types.CLI_REDIRECT_REFUSED,
        title="CLI redirect refused",
        detail="client=cli's redirect_uri must be the registered ephemeral loopback form",
        instance=instance,
        status=400,
    )


def cli_pkce_required_problem(*, instance: str) -> CliPkceRequiredProblem:
    return CliPkceRequiredProblem(
        type=problem_types.CLI_PKCE_REQUIRED,
        title="CLI PKCE required",
        detail="client=cli requires a PKCE code_challenge (S256) — PKCE is mandatory for this public client",
        instance=instance,
        status=400,
    )


def cli_token_refused_problem(*, instance: str) -> CliTokenRefusedProblem:
    return CliTokenRefusedProblem(
        type=problem_types.CLI_TOKEN_REFUSED,
        title="CLI token refused",
        detail="invalid or expired code, PKCE verifier, or redirect_uri",
        instance=instance,
        status=400,
    )


def login_disabled_problem(*, instance: str) -> LoginDisabledProblem:
    return LoginDisabledProblem(
        type=problem_types.LOGIN_DISABLED,
        title="Login disabled",
        detail="login is not enabled",
        instance=instance,
        status=404,
    )


def login_provider_unknown_problem(name: str, *, instance: str) -> LoginProviderUnknownProblem:
    return LoginProviderUnknownProblem(
        type=problem_types.LOGIN_PROVIDER_UNKNOWN,
        title="Unknown login provider",
        detail=f"unknown provider {name!r}",
        instance=instance,
        status=404,
    )


def login_throttled_problem(*, instance: str) -> LoginThrottledProblem:
    return LoginThrottledProblem(
        type=problem_types.LOGIN_THROTTLED,
        title="Login throttled",
        detail="too many login attempts — try again shortly",
        instance=instance,
        status=429,
    )


def login_failed_problem(*, instance: str) -> LoginFailedProblem:
    return LoginFailedProblem(
        type=problem_types.LOGIN_FAILED,
        title="Login failed",
        detail="login_failed",
        instance=instance,
        status=400,
        error="login_failed",
    )


def sso_refused_problem(*, instance: str) -> SsoRefusedProblem:
    return SsoRefusedProblem(
        type=problem_types.SSO_REFUSED,
        title="SSO refused",
        detail="sso_refused",
        instance=instance,
        status=400,
        error="sso_refused",
    )


def tenant_required_problem(*, instance: str) -> TenantRequiredProblem:
    return TenantRequiredProblem(
        type=problem_types.TENANT_REQUIRED,
        title="Tenant required",
        instance=instance,
        status=409,
        detail=TenantRequiredDetail(code="tenant_required", message="name the tenant to act in with X-Blizzard-Tenant"),
    )


def tenant_not_found_problem(*, instance: str) -> TenantNotFoundProblem:
    return TenantNotFoundProblem(
        type=problem_types.TENANT_NOT_FOUND,
        title="Tenant not found",
        detail=NO_SUCH_TENANT,
        instance=instance,
        status=404,
    )


def tenant_credential_mismatch_problem(*, instance: str) -> TenantCredentialMismatchProblem:
    return TenantCredentialMismatchProblem(
        type=problem_types.TENANT_CREDENTIAL_MISMATCH,
        title="Tenant credential mismatch",
        detail="X-Blizzard-Tenant names a tenant other than the presented credential's",
        instance=instance,
        status=400,
    )


def authentication_required_problem(*, instance: str, detail: str) -> AuthenticationRequiredProblem:
    """The caller supplies the applicable existing missing-session or missing-header detail."""
    return AuthenticationRequiredProblem(
        type=problem_types.AUTHENTICATION_REQUIRED,
        title="Authentication required",
        detail=detail,
        instance=instance,
        status=401,
    )


def authentication_revoked_problem(*, instance: str) -> AuthenticationRevokedProblem:
    return AuthenticationRevokedProblem(
        type=problem_types.AUTHENTICATION_REVOKED,
        title="Authentication revoked",
        detail="bearer token has been revoked",
        instance=instance,
        status=401,
    )


def authentication_unresolved_problem(*, instance: str, detail: str) -> AuthenticationUnresolvedProblem:
    """Detail is chosen by the edge for a runner or host, never from a credential."""
    return AuthenticationUnresolvedProblem(
        type=problem_types.AUTHENTICATION_UNRESOLVED,
        title="Authentication unresolved",
        detail=detail,
        instance=instance,
        status=401,
    )


def permission_required_problem(permission: Permission, *, instance: str) -> PermissionRequiredProblem:
    return PermissionRequiredProblem(
        type=problem_types.PERMISSION_REQUIRED,
        title="Permission required",
        detail=f"missing permission {permission!r}",
        instance=instance,
        status=403,
    )


def machine_credential_forbidden_problem(*, instance: str) -> MachineCredentialForbiddenProblem:
    return MachineCredentialForbiddenProblem(
        type=problem_types.MACHINE_CREDENTIAL_FORBIDDEN,
        title="Machine credential forbidden",
        detail="a machine credential is not valid here",
        instance=instance,
        status=403,
    )


def runner_ownership_problem(*, caller_id: str, runner_id: str, instance: str) -> RunnerOwnershipProblem:
    return RunnerOwnershipProblem(
        type=problem_types.RUNNER_OWNERSHIP,
        title="Runner ownership refused",
        detail=f"token belongs to runner {caller_id!r}, not {runner_id!r}",
        instance=instance,
        status=403,
    )


def pinned_graph_missing_problem(*, instance: str) -> PinnedGraphMissingProblem:
    return PinnedGraphMissingProblem(
        type=problem_types.PINNED_GRAPH_MISSING,
        title="Pinned graph missing",
        detail="chunk's pinned graph is missing",
        instance=instance,
        status=500,
    )


def chunk_not_found_problem(chunk_id: str, *, instance: str) -> ChunkNotFoundProblem:
    return ChunkNotFoundProblem(
        type=problem_types.CHUNK_NOT_FOUND,
        title="Chunk not found",
        detail=f"unknown chunk {chunk_id}",
        instance=instance,
        status=404,
    )


def chunk_disappeared_problem(exc: ChunkNotFound, *, instance: str) -> ChunkNotFoundProblem:
    """The pause delegate's under-lock miss has the same detail as its initial lookup."""
    return chunk_not_found_problem(exc.chunk_id, instance=instance)


def chunk_not_pausable_problem(exc: ChunkNotPausable, *, instance: str) -> ChunkNotPausableProblem:
    return ChunkNotPausableProblem(
        type=problem_types.CHUNK_NOT_PAUSABLE,
        title="Chunk not pausable",
        detail=str(exc),
        instance=instance,
        status=409,
    )


def no_run_context_problem(chunk_id: str, *, instance: str) -> NoRunContextProblem:
    return NoRunContextProblem(
        type=problem_types.NO_RUN_CONTEXT,
        title="No run context",
        detail=f"chunk {chunk_id} carries no run context — not a routine run",
        instance=instance,
        status=404,
    )


def no_answered_proposal_problem(chunk_id: str, *, instance: str) -> NoAnsweredProposalProblem:
    return NoAnsweredProposalProblem(
        type=problem_types.NO_ANSWERED_PROPOSAL,
        title="No answered proposal",
        detail=f"chunk {chunk_id} answers no accepted, minted garden proposal",
        instance=instance,
        status=404,
    )


def finding_not_in_chunk_problem(chunk_id: str, finding_id: str, *, instance: str) -> FindingNotInChunkProblem:
    return FindingNotInChunkProblem(
        type=problem_types.FINDING_NOT_IN_CHUNK,
        title="Finding not in chunk",
        detail=f"finding {finding_id} is not among the findings chunk {chunk_id} answers",
        instance=instance,
        status=404,
    )


def question_not_found_problem(question_id: str, *, instance: str) -> QuestionNotFoundProblem:
    return QuestionNotFoundProblem(
        type=problem_types.QUESTION_NOT_FOUND,
        title="Question not found",
        detail=f"unknown question {question_id}",
        instance=instance,
        status=404,
    )


def runner_not_found_problem(runner_id: str, *, instance: str) -> RunnerNotFoundProblem:
    return RunnerNotFoundProblem(
        type=problem_types.RUNNER_NOT_FOUND,
        title="Runner not found",
        detail=f"unknown runner {runner_id}",
        instance=instance,
        status=404,
    )


def runner_not_registered_problem(exc: RunnerNeverConnected, *, instance: str) -> RunnerNotRegisteredProblem:
    return RunnerNotRegisteredProblem(
        type=problem_types.RUNNER_NOT_REGISTERED,
        title="Runner not registered",
        detail=str(exc),
        instance=instance,
        status=409,
    )


def system_artifact_not_found_problem(name: str, *, instance: str) -> SystemArtifactNotFoundProblem:
    return SystemArtifactNotFoundProblem(
        type=problem_types.SYSTEM_ARTIFACT_NOT_FOUND,
        title="System artifact not found",
        detail=f"no system artifact {name!r}",
        instance=instance,
        status=404,
    )


def no_current_node_problem(exc: NoCurrentNode, *, instance: str) -> NoCurrentNodeProblem:
    return NoCurrentNodeProblem(
        type=problem_types.NO_CURRENT_NODE, title="No current node", detail=str(exc), instance=instance, status=409
    )


def lease_ownership_problem(exc: LeaseSegmentsNotOwned, *, instance: str) -> LeaseOwnershipProblem:
    return LeaseOwnershipProblem(
        type=problem_types.LEASE_OWNERSHIP,
        title="Lease ownership refused",
        detail=str(exc),
        instance=instance,
        status=403,
    )


def project_required_problem(exc: ProjectRequired, *, instance: str) -> ProjectRequiredProblem:
    return ProjectRequiredProblem(
        type=problem_types.PROJECT_REQUIRED,
        title="Project required",
        instance=instance,
        status=422,
        detail=ProjectRequiredBody(
            message=str(exc),
            projects=[ProjectRef(project_id=p.project_id, slug=p.slug, name=p.name) for p in exc.projects],
        ),
    )


def unknown_served_project_problem(exc: UnknownServedProject, *, instance: str) -> UnknownServedProjectProblem:
    return UnknownServedProjectProblem(
        type=problem_types.UNKNOWN_SERVED_PROJECT,
        title="Unknown served project",
        detail=str(exc),
        instance=instance,
        status=422,
    )


def runner_retired_problem(exc: RunnerRetired, *, instance: str, status_code: int = 403) -> RunnerRetiredProblem:
    return RunnerRetiredProblem(
        type=problem_types.RUNNER_RETIRED,
        title="Runner retired",
        detail=str(exc),
        instance=instance,
        status=status_code,
    )


def host_retired_problem(exc: HostRetired, *, instance: str, status_code: int = 403) -> HostRetiredProblem:
    return HostRetiredProblem(
        type=problem_types.HOST_RETIRED, title="Host retired", detail=str(exc), instance=instance, status=status_code
    )


def host_name_taken_problem(exc: HostNameTaken, *, instance: str) -> HostNameTakenProblem:
    return HostNameTakenProblem(
        type=problem_types.HOST_NAME_TAKEN, title="Host name taken", detail=str(exc), instance=instance, status=409
    )


def runner_already_hosted_problem(exc: RunnerAlreadyHosted, *, instance: str) -> RunnerAlreadyHostedProblem:
    return RunnerAlreadyHostedProblem(
        type=problem_types.RUNNER_ALREADY_HOSTED,
        title="Runner already hosted",
        detail=str(exc),
        instance=instance,
        status=409,
    )


def runner_lifecycle_conflict_problem(exc: RunnerLifecycleConflict, *, instance: str) -> RunnerLifecycleConflictProblem:
    return RunnerLifecycleConflictProblem(
        type=problem_types.RUNNER_LIFECYCLE_CONFLICT,
        title="Runner lifecycle conflict",
        detail=str(exc),
        instance=instance,
        status=409,
    )


def runner_holds_routes_problem(exc: RunnerHoldsRoutes, *, instance: str) -> RunnerHoldsRoutesProblem:
    return RunnerHoldsRoutesProblem(
        type=problem_types.RUNNER_HOLDS_ROUTES,
        title="Runner holds routes",
        detail=str(exc),
        instance=instance,
        status=409,
    )


def runner_retire_interrupted_problem(exc: RetireInterrupted, *, instance: str) -> RunnerRetireInterruptedProblem:
    return RunnerRetireInterruptedProblem(
        type=problem_types.RUNNER_RETIRE_INTERRUPTED,
        title="Runner retirement interrupted",
        detail=str(exc),
        instance=instance,
        status=409,
    )


def claim_conflict_problem(exc: ClaimConflict, *, chunk_id: str, instance: str) -> RouteClaimConflictProblem:
    """Map a lost claim race without exposing the exception message."""
    return RouteClaimConflictProblem(
        type=CLAIM_CONFLICT,
        title="Claim conflict",
        detail="chunk already claimed",
        instance=instance,
        chunk_id=chunk_id,
        held_by_runner_id=exc.held_by_runner_id,
    )


def claim_terminal_problem(exc: ClaimDeniedTerminal, *, instance: str) -> RouteClaimTerminalProblem:
    """Preserve the released chunk-status string, not the HTTP status."""
    return RouteClaimTerminalProblem(
        type=CLAIM_TERMINAL,
        title="Claim denied: terminal chunk",
        detail="chunk is terminal",
        instance=instance,
        chunk_id=exc.chunk_id,
        status=exc.status.value,
    )


def claim_not_ready_problem(exc: ClaimDeniedNotReady, *, instance: str) -> RouteClaimNotReadyProblem:
    return RouteClaimNotReadyProblem(
        type=CLAIM_NOT_READY,
        title="Claim denied: chunk not ready",
        detail=str(exc),
        instance=instance,
        chunk_id=exc.chunk_id,
        status=exc.status.value,
    )


def claim_dependency_problem(exc: ClaimDeniedDependency, *, instance: str) -> RouteClaimDependencyProblem:
    """Map an unmet prerequisite without adding a status member."""
    return RouteClaimDependencyProblem(
        type=CLAIM_DEPENDENCY,
        title="Claim denied: unmet prerequisite",
        detail="chunk depends on an unmet prerequisite",
        instance=instance,
        chunk_id=exc.chunk_id,
        prerequisite_chunk_id=exc.prerequisite_chunk_id,
    )


def claim_incompatible_problem(
    exc: ClaimDeniedIncompatible, *, instance: str, project_name: str | None = None
) -> RouteClaimIncompatibleProblem:
    """The edge resolves the project slug; its absence retains the route's id fallback."""
    if exc.reason is ClaimDenialReason.PROJECT and exc.project_id is not None:
        reason = ClaimDenialReason.PROJECT
        detail = (
            f"runner does not serve the chunk's project {project_name if project_name is not None else exc.project_id}"
        )
    else:
        reason = ClaimDenialReason.CAPABILITY
        detail = "runner capabilities no longer satisfy the chunk's reachable lineage"
    return RouteClaimIncompatibleProblem(
        type=CLAIM_INCOMPATIBLE,
        title="Claim denied: incompatible runner",
        detail=detail,
        instance=instance,
        chunk_id=exc.chunk_id,
        incompatible_runner_id=exc.runner_id,
        reason=reason,
    )


def claim_paused_problem(exc: ClaimDeniedPaused, *, chunk_id: str, instance: str) -> RouteClaimPausedProblem:
    return RouteClaimPausedProblem(
        type=CLAIM_PAUSED,
        title="Claim denied: paused runner",
        detail="runner is paused at the hub",
        instance=instance,
        status=403,
        chunk_id=chunk_id,
        runner_id=exc.runner_id,
    )


def claim_runner_retired_problem(exc: RunnerRetired, *, chunk_id: str, instance: str) -> ClaimRunnerRetiredProblem:
    return ClaimRunnerRetiredProblem(
        type=CLAIM_RUNNER_RETIRED,
        title="Claim denied: retired runner",
        detail=str(exc),
        instance=instance,
        status=403,
        chunk_id=chunk_id,
        runner_id=exc.runner_id,
    )


def claim_runner_unregistered_problem(
    exc: ClaimDeniedUnregistered, *, chunk_id: str, instance: str
) -> ClaimRunnerUnregisteredProblem:
    return ClaimRunnerUnregisteredProblem(
        type=CLAIM_RUNNER_UNREGISTERED,
        title="Claim denied: unregistered runner",
        detail=str(exc),
        instance=instance,
        status=403,
        chunk_id=chunk_id,
        runner_id=exc.runner_id,
    )


def claim_runner_version_problem(
    exc: ClaimDeniedVersionRefused, *, chunk_id: str, instance: str
) -> ClaimRunnerVersionRefusedProblem:
    return ClaimRunnerVersionRefusedProblem(
        type=CLAIM_RUNNER_VERSION_REFUSED,
        title="Claim denied: runner version refused",
        detail=str(exc),
        instance=instance,
        status=403,
        chunk_id=chunk_id,
        runner_id=exc.runner_id,
    )


def rekey_not_holder_problem(
    exc: RekeyDeniedNotHolder, *, caller_id: str, instance: str
) -> RouteNotFoundProblem | RouteNotHeldProblem:
    """Preserve the route's pre-check response, including its ownership mismatch detail."""
    if exc.holder is None:
        return RouteNotFoundProblem(
            type=ROUTE_NOT_FOUND, title="Route not found", detail=str(exc), instance=instance, status=404
        )
    detail = str(exc) if exc.holder == caller_id else f"token belongs to runner {caller_id!r}, not {exc.holder!r}"
    return RouteNotHeldProblem(
        type=ROUTE_NOT_HELD, title="Route not held", detail=detail, instance=instance, status=403
    )


def rekey_terminal_problem(exc: RekeyDeniedTerminal, *, instance: str) -> RouteTerminalProblem:
    return RouteTerminalProblem(
        type=ROUTE_TERMINAL, title="Route terminal", detail=str(exc), instance=instance, status=409
    )


def release_not_holder_problem(exc: ReleaseRefused, *, instance: str) -> RouteNotHeldProblem:
    """Use only for the release service's NOT_HOLDER path."""
    return RouteNotHeldProblem(
        type=ROUTE_NOT_HELD, title="Route not held", detail=str(exc), instance=instance, status=403
    )


def release_problem(exc: ReleaseRefused, *, instance: str) -> ReleaseProblem | None:
    """Map only 409 release refusals; not-holder remains the distinct 403 path."""
    reason = {
        ReleaseRefusalKind.STALE: ReleaseRefusalReason.STALE,
        ReleaseRefusalKind.NOT_RUNNING: ReleaseRefusalReason.NOT_RUNNING,
    }.get(exc.kind)
    if reason is None:
        return None
    return ReleaseProblem(
        type=RELEASE_REFUSAL,
        title="Release refused",
        detail=str(exc),
        instance=instance,
        status=409,
        chunk_id=exc.chunk_id,
        reason=reason,
    )


def runner_identity_problem(exc: RunnerTokenRefused, *, instance: str) -> RunnerIdentityProblem:
    return RunnerIdentityProblem(
        type=RUNNER_IDENTITY,
        title="Runner identity refused",
        detail="runner identity refused",
        instance=instance,
        status=401,
        reason=exc.reason,
        runner_id=exc.runner_id,
    )


def runner_version_problem(exc: RunnerVersionRefused, *, instance: str) -> RunnerVersionProblem:
    return RunnerVersionProblem(
        type=RUNNER_VERSION,
        title="Runner version refused",
        detail=exc.refusal.detail,
        instance=instance,
        status=409,
        reason=exc.refusal.reason,
        runner_version=exc.judgement.reported_version,
        hub_version=exc.judgement.hub_version,
        floor=exc.judgement.floor,
    )


def malformed_cursor_problem(exc: MalformedCursor, *, instance: str) -> MalformedCursorProblem:
    """Never echo the untrusted cursor in the response."""
    return MalformedCursorProblem(
        type=MALFORMED_CURSOR, title="Malformed cursor", detail="malformed cursor", instance=instance, status=422
    )


def invariants_in_progress_problem(*, instance: str) -> InvariantsInProgressProblem:
    return InvariantsInProgressProblem(
        type=INVARIANTS_IN_PROGRESS,
        title="Invariant check in progress",
        detail="another invariant check is running — try again when it finishes",
        instance=instance,
        status=409,
    )

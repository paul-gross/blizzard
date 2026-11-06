"""The HTTP problem seam preserves wire-defined shapes and response metadata."""

from __future__ import annotations

import json
from typing import Any, cast
from unittest.mock import MagicMock

import pytest
from fastapi import HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from blizzard.foundation.chunk_status import ChunkStatus
from blizzard.foundation.claim_denials import ClaimDenialReason
from blizzard.foundation.runner_tokens import RunnerTokenRefusalReason
from blizzard.foundation.runner_version import VersionRefusalReason
from blizzard.hub.api import fleet, idp, problems, system_artifacts
from blizzard.hub.api.auth import RunnerPrincipal
from blizzard.hub.api.problem_declarations import (
    HUB_PROBLEM_DECLARATIONS,
    PROXY_RESOURCE_DECLARATIONS,
    ProblemDeclaration,
    problem_declarations_by_status,
)
from blizzard.hub.api.problem_rendering import problem_response
from blizzard.hub.api.problems import (
    claim_conflict_problem,
    claim_dependency_problem,
    claim_incompatible_problem,
    claim_not_ready_problem,
    claim_paused_problem,
    claim_runner_retired_problem,
    claim_runner_unregistered_problem,
    claim_runner_version_problem,
    claim_terminal_problem,
    malformed_cursor_problem,
    rekey_not_holder_problem,
    rekey_terminal_problem,
    release_not_holder_problem,
    release_problem,
    runner_identity_problem,
    runner_version_problem,
)
from blizzard.hub.auth.permissions import Permission
from blizzard.hub.composition import TenantServices
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
from blizzard.hub.domain.execution.fleet import RetireInterrupted, RetireOutcome
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
from blizzard.hub.domain.runners.version import RunnerVersionRefused, VersionJudgement, VersionRefusal
from blizzard.wire.problem import CLAIM_CONFLICT, CLAIM_DEPENDENCY, CLAIM_TERMINAL, CommonProblemMembers
from blizzard.wire.route import RouteClaimConflictProblem, RouteClaimDependencyProblem, RouteClaimTerminalProblem
from blizzard.wire.runner import RunnerIdentityProblem


class TerminalProblem(CommonProblemMembers[str, str]):
    status: str


class NonterminalProblem(CommonProblemMembers[str, dict[str, str]]):
    diagnostic: str | None = None
    request_id: str = Field(serialization_alias="requestId")


def test_lease_segments_invariant_failure_remains_an_undeclared_http_error() -> None:
    services = MagicMock()
    failure = RuntimeError("inconsistent lease")
    services.transcripts.runner_id_for_lease.side_effect = failure
    with pytest.raises(HTTPException) as caught:
        fleet.get_lease_transcript_segments(
            "c", "n", 1, cast(TenantServices, services), cast(RunnerPrincipal, MagicMock())
        )
    assert caught.value.status_code == 500
    assert caught.value.__cause__ is failure
    declarations = problem_declarations_by_status(
        HUB_PROBLEM_DECLARATIONS, method="GET", path="/api/fleet/chunks/{chunk_id}/transcript-segments"
    )
    assert 500 not in declarations


@pytest.mark.parametrize(
    "instance", ["/api/hosts?token=secret#fragment", "https://user:secret@example.com/api/hosts?token=secret"]
)
def test_response_strips_instance_secrets(instance: str) -> None:
    body = json.loads(bytes(problem_response(problems.tenant_not_found_problem(instance=instance)).body))
    assert body["instance"] == "/api/hosts"
    assert body["detail"] == "no such tenant"


def test_response_rejects_unrelated_model_with_sensitive_instance() -> None:
    class Unrelated(BaseModel):
        instance: str

    unrelated = Unrelated(instance="/api/hosts?token=secret")
    with pytest.raises(TypeError, match="CommonProblemMembers"):
        problem_response(cast(CommonProblemMembers[Any, Any], unrelated), status_code=400)


def test_response_preserves_legacy_string_status_and_headers() -> None:
    response = problem_response(
        TerminalProblem(type="urn:example:terminal", title="Terminal", detail="terminal", instance="/", status="done"),
        status_code=409,
        headers={"WWW-Authenticate": "Bearer", "Retry-After": "30"},
    )
    assert response.status_code == 409
    assert response.headers["content-type"] == "application/problem+json"
    assert response.headers["www-authenticate"] == "Bearer"
    assert response.headers["retry-after"] == "30"
    assert json.loads(bytes(response.body)) == {
        "type": "urn:example:terminal",
        "title": "Terminal",
        "detail": "terminal",
        "instance": "/",
        "status": "done",
    }


def test_response_derives_status_from_an_integer_status_member() -> None:
    problem = problems.tenant_not_found_problem(instance="/")
    assert problem_response(problem).status_code == 404
    with pytest.raises(TypeError, match="carries its own HTTP status"):
        problem_response(problem, status_code=404)


def test_response_requires_a_status_code_without_an_integer_status_member() -> None:
    problem = TerminalProblem(
        type="urn:example:terminal", title="Terminal", detail="terminal", instance="/", status="done"
    )
    with pytest.raises(TypeError, match="needs a status_code"):
        problem_response(problem)


def test_response_preserves_absence_null_object_detail_and_aliases() -> None:
    response = problem_response(
        NonterminalProblem(
            type="urn:example:race", title="Race", detail={"reason": "race"}, instance="/", request_id="r1"
        ),
        status_code=409,
    )
    assert json.loads(bytes(response.body)) == {
        "type": "urn:example:race",
        "title": "Race",
        "detail": {"reason": "race"},
        "instance": "/",
        "diagnostic": None,
        "requestId": "r1",
    }


def test_response_omits_null_status_but_keeps_null_extension() -> None:
    problem = RunnerIdentityProblem(
        type="urn:blizzard:problem:runner-identity",
        title="Refused",
        detail="runner identity refused",
        instance="/api/fleet/identity",
        reason=RunnerTokenRefusalReason.MISSING,
        runner_id=None,
    )
    body = json.loads(bytes(problem_response(problem, status_code=401).body))
    assert "status" not in body
    assert body["runner_id"] is None


def test_declarations_select_operation_and_preserve_all_distinct_variants() -> None:
    declarations = [
        ProblemDeclaration("POST", "/claims", 409, TerminalProblem),
        ProblemDeclaration("POST", "/claims", 409, NonterminalProblem),
        ProblemDeclaration("POST", "/claims", 409, TerminalProblem),
        ProblemDeclaration("GET", "/claims", 404, TerminalProblem),
        ProblemDeclaration("POST", "/other", 403, TerminalProblem),
    ]
    assert problem_declarations_by_status(declarations, method="post", path="/claims") == {
        409: (TerminalProblem, NonterminalProblem)
    }
    assert problem_declarations_by_status(declarations, method="GET", path="/missing") == {}


def test_claim_problem_mappings_preserve_released_fields() -> None:
    instance = "/api/fleet/routes"
    terminal = claim_terminal_problem(ClaimDeniedTerminal(chunk_id="chunk", status=ChunkStatus.DONE), instance=instance)
    conflict = claim_conflict_problem(ClaimConflict(held_by_runner_id="runner"), chunk_id="chunk", instance=instance)
    dependency = claim_dependency_problem(
        ClaimDeniedDependency(chunk_id="chunk", prerequisite_chunk_id="prerequisite"), instance=instance
    )
    for problem, model, uri in (
        (terminal, RouteClaimTerminalProblem, CLAIM_TERMINAL),
        (conflict, RouteClaimConflictProblem, CLAIM_CONFLICT),
        (dependency, RouteClaimDependencyProblem, CLAIM_DEPENDENCY),
    ):
        response = problem_response(problem, status_code=409)
        body = json.loads(bytes(response.body))
        assert response.headers["content-type"] == "application/problem+json"
        assert body["type"] == uri
        assert body["instance"] == instance
        assert model.model_validate(body) == problem
    assert terminal.model_dump()["status"] == "done"
    assert terminal.detail == "chunk is terminal"
    assert conflict.model_dump()["held_by_runner_id"] == "runner"
    assert conflict.detail == "chunk already claimed"
    assert dependency.model_dump()["prerequisite_chunk_id"] == "prerequisite"
    assert dependency.detail == "chunk depends on an unmet prerequisite"
    assert "status" not in conflict.model_dump()
    assert "status" not in dependency.model_dump()


def test_declarations_accept_real_wire_variants() -> None:
    declarations = [
        ProblemDeclaration("POST", "/api/fleet/routes", 409, RouteClaimTerminalProblem),
        ProblemDeclaration("POST", "/api/fleet/routes", 409, RouteClaimDependencyProblem),
        ProblemDeclaration("POST", "/api/fleet/routes", 409, RouteClaimConflictProblem),
    ]
    assert problem_declarations_by_status(declarations, method="POST", path="/api/fleet/routes") == {
        409: (RouteClaimTerminalProblem, RouteClaimDependencyProblem, RouteClaimConflictProblem)
    }


def test_proxy_resource_declarations_cover_actual_delegated_refusals() -> None:
    scope = problems.chunk_not_found_problem("c", instance="/api/fleet/scopes")
    assert (scope.status, scope.detail) == (404, "unknown chunk c")

    packaged = MagicMock()
    packaged.named.return_value = None
    with pytest.raises(HTTPException) as artifact_error:
        system_artifacts.get_system_artifact("a/b", packaged)
    artifact = problems.system_artifact_not_found_problem("a/b", instance="/api/fleet/system-artifacts/a/b")
    assert (artifact.status, artifact.detail) == (artifact_error.value.status_code, artifact_error.value.detail)

    for path, model in (
        ("/api/fleet/scopes", type(scope)),
        ("/api/fleet/system-artifacts/{name:path}", type(artifact)),
    ):
        assert model in problem_declarations_by_status(PROXY_RESOURCE_DECLARATIONS, method="GET", path=path)[404]


def test_graph_declarations_cover_every_explicit_fleet_missing_graph() -> None:
    for method, path in (
        ("GET", "/api/fleet/chunks/{chunk_id}/envelope"),
        ("POST", "/api/fleet/chunks/{chunk_id}/hub-advance"),
        ("POST", "/api/fleet/routes"),
        ("POST", "/api/fleet/chunks/{chunk_id}/completions"),
        ("POST", "/api/fleet/chunks/{chunk_id}/decisions"),
    ):
        assert (
            type(problems.pinned_graph_missing_problem(instance=path))
            in problem_declarations_by_status(PROXY_RESOURCE_DECLARATIONS, method=method, path=path)[500]
        )


def test_dependency_declarations_distinguish_claim_and_identity() -> None:
    claim = problem_declarations_by_status(HUB_PROBLEM_DECLARATIONS, method="POST", path="/api/fleet/routes")
    assert problems.AuthenticationRevokedProblem in claim[401]
    assert problems.TenantCredentialMismatchProblem in claim[400]
    assert problems.ClaimRunnerRetiredProblem in claim[403]
    assert problems.RunnerRetiredProblem not in claim[403]
    identity = problem_declarations_by_status(HUB_PROBLEM_DECLARATIONS, method="GET", path="/api/fleet/identity")
    assert identity == {401: (RunnerIdentityProblem,), 503: (problems.HubStoreUnavailableProblem,)}
    host = problem_declarations_by_status(HUB_PROBLEM_DECLARATIONS, method="POST", path="/api/hosts")
    assert problems.TenantRequiredProblem in host[409]
    assert problems.MachineCredentialForbiddenProblem in host[403]


def test_garden_read_declares_both_shared_run_context_refusals() -> None:
    for path in (
        "/api/fleet/chunks/{chunk_id}/garden/findings",
        "/api/fleet/chunks/{chunk_id}/analytics/counts/files",
    ):
        variants = problem_declarations_by_status(HUB_PROBLEM_DECLARATIONS, method="GET", path=path)[404]
        assert problems.ChunkNotFoundProblem in variants
        assert problems.NoRunContextProblem in variants


def test_delegated_pause_race_maps_real_error_without_changing_detail() -> None:
    exc = ChunkNotFound("c")
    problem = problems.chunk_disappeared_problem(exc, instance="/api/fleet/chunks/c/pause")
    assert problem.detail == str(exc) == "unknown chunk c"
    variants = problem_declarations_by_status(
        HUB_PROBLEM_DECLARATIONS, method="POST", path="/api/fleet/chunks/{chunk_id}/pause"
    )
    assert type(problem) in variants[404]


def test_real_idp_refusals_render_their_declared_problems() -> None:
    request = MagicMock()
    request.url.path = "/api/auth/jwks.json"
    request.app.state.config.auth.mode = "none"
    disabled = idp.jwks(request)
    request.url.path = "/api/auth/authorize"
    request.app.state.config.auth.mode = "oauth"
    response_mode = idp.authorize(request, client="r", redirect_uri="/callback", state="s", response_mode="invalid")
    assert isinstance(disabled, JSONResponse)
    assert isinstance(response_mode, JSONResponse)
    assert (
        json.loads(bytes(disabled.body)) == problems.idp_disabled_problem(instance="/api/auth/jwks.json").problem_dump()
    )
    assert (
        json.loads(bytes(response_mode.body))
        == problems.federation_response_mode_problem("invalid", instance="/api/auth/authorize").problem_dump()
    )


def test_federation_problem_mappings_and_declarations() -> None:
    cases = [
        ("GET", "/api/auth/authorize", problems.idp_disabled_problem(instance="/"), 404),
        ("GET", "/api/auth/jwks.json", problems.idp_disabled_problem(instance="/"), 404),
        ("GET", "/api/auth/authorize", problems.federation_response_mode_problem("bad", instance="/"), 400),
        ("GET", "/api/auth/authorize", problems.federation_client_refused_problem(instance="/"), 400),
        ("GET", "/api/auth/authorize", problems.federation_no_provider_problem(instance="/"), 501),
        ("GET", "/api/auth/authorize", problems.cli_redirect_refused_problem(instance="/"), 400),
        ("GET", "/api/auth/authorize", problems.cli_pkce_required_problem(instance="/"), 400),
        ("POST", "/api/auth/cli/token", problems.cli_token_refused_problem(instance="/"), 400),
        ("GET", "/api/auth/{name}/authorize", problems.login_disabled_problem(instance="/"), 404),
        ("GET", "/api/auth/{name}/authorize", problems.login_provider_unknown_problem("x", instance="/"), 404),
        ("GET", "/api/auth/{name}/authorize", problems.login_throttled_problem(instance="/"), 429),
        ("GET", "/api/auth/{name}/callback", problems.login_failed_problem(instance="/"), 400),
        ("GET", "/api/auth/{name}/callback", problems.sso_refused_problem(instance="/"), 400),
        ("POST", "/api/hosts", problems.runner_credential_forbidden_problem("r", instance="/"), 403),
    ]
    for method, path, problem, status in cases:
        body = json.loads(bytes(problem_response(problem).body))
        assert body["status"] == status
        assert (
            type(problem) in problem_declarations_by_status(HUB_PROBLEM_DECLARATIONS, method=method, path=path)[status]
        )
    assert problems.federation_response_mode_problem("bad", instance="/").detail == "unknown response_mode 'bad'"
    assert problems.login_provider_unknown_problem("x", instance="/").detail == "unknown provider 'x'"
    assert (
        problems.runner_credential_forbidden_problem("r", instance="/").detail
        == "runner token for 'r' is not valid on an operator verb"
    )
    for problem, error in (
        (problems.login_failed_problem(instance="/"), "login_failed"),
        (problems.sso_refused_problem(instance="/"), "sso_refused"),
    ):
        assert json.loads(bytes(problem_response(problem).body))["error"] == error


def test_incompatible_claim_preserves_capability_and_project_details() -> None:
    capability = claim_incompatible_problem(ClaimDeniedIncompatible(chunk_id="c", runner_id="r"), instance="/routes")
    project_error = ClaimDeniedIncompatible(
        chunk_id="c", runner_id="r", reason=ClaimDenialReason.PROJECT, project_id="p"
    )
    project = claim_incompatible_problem(project_error, instance="/routes", project_name="slug")
    assert capability.model_dump(mode="json")["reason"] == "capability"
    assert capability.detail == "runner capabilities no longer satisfy the chunk's reachable lineage"
    assert project.model_dump(mode="json")["reason"] == "project"
    assert project.detail == "runner does not serve the chunk's project slug"
    assert claim_incompatible_problem(project_error, instance="/routes").detail.endswith("project p")
    assert "status" not in project.model_dump()
    assert project.model_dump()["incompatible_runner_id"] == "r"


def test_paused_claim_preserves_legacy_fields() -> None:
    problem = claim_paused_problem(ClaimDeniedPaused(runner_id="r"), chunk_id="c", instance="/routes")
    assert problem.model_dump(mode="json") == {
        "type": "urn:blizzard:problem:claim-paused",
        "title": "Claim denied: paused runner",
        "detail": "runner is paused at the hub",
        "instance": "/routes",
        "status": 403,
        "chunk_id": "c",
        "runner_id": "r",
    }


def test_release_maps_only_conflicts_and_preserves_reason_and_detail() -> None:
    for kind in (ReleaseRefusalKind.STALE, ReleaseRefusalKind.NOT_RUNNING):
        exc = ReleaseRefused("c", kind, f"release refused: {kind}")
        problem = release_problem(exc, instance="/release")
        assert problem is not None
        body = problem.model_dump(mode="json")
        assert (body["chunk_id"], body["reason"], body["detail"], body["status"]) == ("c", kind.value, str(exc), 409)
    assert release_problem(ReleaseRefused("c", ReleaseRefusalKind.NOT_HOLDER, "secret"), instance="/release") is None


def test_identity_and_version_mappings_keep_nullable_and_discriminator_fields() -> None:
    for reason, runner_id in ((RunnerTokenRefusalReason.MISSING, None), (RunnerTokenRefusalReason.REVOKED, "r")):
        body = runner_identity_problem(
            RunnerTokenRefused(reason, runner_id=runner_id), instance="/identity"
        ).model_dump(mode="json")
        assert (body["reason"], body["runner_id"], body["status"]) == (reason.value, runner_id, 401)
    refusal = VersionRefusal(reason=VersionRefusalReason.UNVERSIONED, detail="upgrade runner")
    judgement = VersionJudgement(None, refusal, "0.4.0", "0.2", "0.4")
    problem = runner_version_problem(RunnerVersionRefused("r", judgement, refusal, changed=False), instance="/register")
    body = problem.model_dump(mode="json")
    assert (body["reason"], body["runner_version"], body["hub_version"], body["floor"], body["detail"]) == (
        "unversioned",
        None,
        "0.4.0",
        "0.2",
        "upgrade runner",
    )


def test_malformed_cursor_does_not_echo_input() -> None:
    problem = malformed_cursor_problem(MalformedCursor("secret"), instance="/items")
    assert problem.detail == "malformed cursor"
    assert "secret" not in problem.model_dump_json()
    assert problem.status == 422


def test_additional_claim_errors_preserve_the_existing_denial_bodies() -> None:
    not_ready = ClaimDeniedNotReady(chunk_id="c", status=ChunkStatus.PAUSED)
    result = claim_not_ready_problem(not_ready, instance="/routes").model_dump(mode="json")
    assert (result["type"], result["chunk_id"], result["status"], result["detail"]) == (
        "urn:blizzard:problem:claim-not-ready",
        "c",
        "paused",
        str(not_ready),
    )
    retired = RunnerRetired("r", action="claim")
    unregistered = ClaimDeniedUnregistered(runner_id="r")
    version = ClaimDeniedVersionRefused(runner_id="r", reason=VersionRefusalReason.TOO_OLD, version="0.1")
    cases = [
        (retired, claim_runner_retired_problem(retired, chunk_id="c", instance="/routes"), "claim-runner-retired"),
        (
            unregistered,
            claim_runner_unregistered_problem(unregistered, chunk_id="c", instance="/routes"),
            "claim-runner-unregistered",
        ),
        (
            version,
            claim_runner_version_problem(version, chunk_id="c", instance="/routes"),
            "claim-runner-version-refused",
        ),
    ]
    for exc, problem, suffix in cases:
        body = problem.model_dump(mode="json")
        assert (body["type"], body["detail"], body["status"], body["chunk_id"], body["runner_id"]) == (
            f"urn:blizzard:problem:{suffix}",
            str(exc),
            403,
            "c",
            "r",
        )


def test_rekey_and_release_not_holder_mappings_preserve_detail() -> None:
    for holder, status, uri in (
        (None, 404, "route-not-found"),
        ("other", 403, "route-not-held"),
        ("self", 403, "route-not-held"),
    ):
        exc = RekeyDeniedNotHolder(chunk_id="c", holder=holder)
        body = rekey_not_holder_problem(exc, caller_id="self", instance="/rekey").model_dump(mode="json")
        detail = "token belongs to runner 'self', not 'other'" if holder == "other" else str(exc)
        assert (body["type"], body["status"], body["detail"]) == (f"urn:blizzard:problem:{uri}", status, detail)
    exc_terminal = RekeyDeniedTerminal(chunk_id="c", status=ChunkStatus.DONE)
    body = rekey_terminal_problem(exc_terminal, instance="/rekey").model_dump(mode="json")
    assert (body["status"], body["detail"]) == (409, str(exc_terminal))
    exc_release = ReleaseRefused("c", ReleaseRefusalKind.NOT_HOLDER, "route token mismatch")
    body = release_not_holder_problem(exc_release, instance="/release").model_dump(mode="json")
    assert (body["status"], body["detail"]) == (403, str(exc_release))


def test_tenant_and_authentication_problem_bodies() -> None:
    tenant = problems.tenant_required_problem(instance="/api/me").model_dump(mode="json")
    assert tenant["detail"] == {
        "code": "tenant_required",
        "message": "name the tenant to act in with X-Blizzard-Tenant",
    }
    assert tenant["status"] == 409
    assert problems.tenant_not_found_problem(instance="/api/hosts").detail == "no such tenant"
    assert problems.tenant_credential_mismatch_problem(instance="/api/fleet/routes").status == 400
    assert (
        problems.authentication_required_problem(instance="/", detail="authentication required").detail
        == "authentication required"
    )
    assert problems.authentication_revoked_problem(instance="/").detail == "bearer token has been revoked"
    assert (
        problems.authentication_unresolved_problem(
            instance="/", detail="bearer token does not resolve to a known host"
        ).status
        == 401
    )
    assert problems.permission_required_problem(Permission.FLEET_VIEW, instance="/").detail == (
        f"missing permission {Permission.FLEET_VIEW!r}"
    )
    assert (
        problems.machine_credential_forbidden_problem(instance="/").detail == "a machine credential is not valid here"
    )
    assert problems.runner_ownership_problem(caller_id="a", runner_id="b", instance="/").detail == (
        "token belongs to runner 'a', not 'b'"
    )


def test_resource_and_domain_problem_details() -> None:
    assert problems.pinned_graph_missing_problem(instance="/").status == 500
    assert problems.chunk_not_found_problem("c", instance="/").detail == "unknown chunk c"
    assert problems.system_artifact_not_found_problem("x/y", instance="/").detail == "no system artifact 'x/y'"
    assert (
        problems.no_current_node_problem(NoCurrentNode("c"), instance="/").detail
        == "chunk has no current runner node (terminal)"
    )
    lease = problems.lease_ownership_problem(
        LeaseSegmentsNotOwned(owning_runner_id="secret-owner", requesting_runner_id="r"), instance="/"
    )
    assert lease.detail == "lease segments belong to another runner"
    assert "secret-owner" not in lease.model_dump_json()
    detail = problems.project_required_problem(ProjectRequired(()), instance="/").model_dump(mode="json")["detail"]
    assert detail == {"code": "project_required", "message": "name a project (this tenant holds: none)", "projects": []}
    assert (
        problems.unknown_served_project_problem(UnknownServedProject(("missing",)), instance="/").detail
        == "no project answers to missing"
    )


def test_fleet_and_delegated_resource_refusals() -> None:
    paused = ChunkNotPausable("c", ChunkStatus.DONE)
    never_connected = RunnerNeverConnected("r")
    cases = [
        (
            problems.pinned_graph_missing_problem(instance="/fleet"),
            500,
            "pinned-graph-missing",
            "chunk's pinned graph is missing",
        ),
        (problems.chunk_not_pausable_problem(paused, instance="/fleet"), 409, "chunk-not-pausable", str(paused)),
        (
            problems.no_run_context_problem("c", instance="/fleet"),
            404,
            "no-run-context",
            "chunk c carries no run context — not a routine run",
        ),
        (
            problems.no_answered_proposal_problem("c", instance="/fleet"),
            404,
            "no-answered-proposal",
            "chunk c answers no accepted, minted garden proposal",
        ),
        (
            problems.finding_not_in_chunk_problem("c", "f", instance="/fleet"),
            404,
            "finding-not-in-chunk",
            "finding f is not among the findings chunk c answers",
        ),
        (problems.question_not_found_problem("q", instance="/fleet"), 404, "question-not-found", "unknown question q"),
        (problems.runner_not_found_problem("r", instance="/fleet"), 404, "runner-not-found", "unknown runner r"),
        (
            problems.runner_not_registered_problem(never_connected, instance="/fleet"),
            409,
            "runner-not-registered",
            str(never_connected),
        ),
        (
            problems.system_artifact_not_found_problem("a/b", instance="/fleet"),
            404,
            "system-artifact-not-found",
            "no system artifact 'a/b'",
        ),
    ]
    for problem, status, suffix, detail in cases:
        response = problem_response(problem)
        body = json.loads(bytes(response.body))
        assert response.headers["content-type"] == "application/problem+json"
        assert response.status_code == body["status"] == status
        assert body["type"] == f"urn:blizzard:problem:{suffix}"
        assert body["detail"] == detail
        assert body["instance"] == "/fleet"


def test_lifecycle_problem_details_and_contextual_status() -> None:
    cases = [
        (
            RunnerRetired("r", action="fleet call"),
            problems.runner_retired_problem(RunnerRetired("r", action="fleet call"), instance="/"),
            403,
        ),
        (
            HostRetired("h", action="host call"),
            problems.host_retired_problem(HostRetired("h", action="host call"), instance="/"),
            403,
        ),
        (HostNameTaken("taken"), problems.host_name_taken_problem(HostNameTaken("taken"), instance="/"), 409),
        (RunnerAlreadyHosted("r"), problems.runner_already_hosted_problem(RunnerAlreadyHosted("r"), instance="/"), 409),
        (
            RunnerLifecycleConflict("r", expected=1),
            problems.runner_lifecycle_conflict_problem(RunnerLifecycleConflict("r", expected=1), instance="/"),
            409,
        ),
        (
            RunnerHoldsRoutes("r", []),
            problems.runner_holds_routes_problem(RunnerHoldsRoutes("r", []), instance="/"),
            409,
        ),
    ]
    for exc, problem, status in cases:
        assert problem.detail == str(exc)
        assert problem.status == status
    assert (
        problems.host_retired_problem(HostRetired("h", action="adoption"), instance="/", status_code=409).status == 409
    )


def test_interrupted_retirement_maps_actual_exception_and_declaration() -> None:
    conflict = RunnerLifecycleConflict("r", expected=1)
    exc = RetireInterrupted(RetireOutcome(fact_id=1, revocation_id=None, released=()), conflict)
    path = "/api/fleet/host/runners/{runner_id}/retire"
    problem = problems.runner_retire_interrupted_problem(exc, instance="/api/fleet/host/runners/r/retire")
    response = problem_response(problem)
    body = json.loads(bytes(response.body))
    assert (body["type"], body["detail"], body["status"]) == (
        "urn:blizzard:problem:runner-retire-interrupted",
        str(conflict),
        409,
    )
    assert type(problem) in problem_declarations_by_status(PROXY_RESOURCE_DECLARATIONS, method="POST", path=path)[409]

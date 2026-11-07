"""Problem serialization preserves the legacy claim status distinction."""

import importlib
import inspect
import json
import pkgutil
from typing import get_args

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import BaseModel, TypeAdapter, ValidationError

import blizzard.wire as wire
from blizzard.wire import problem as registry
from blizzard.wire import problem_types_admin, problem_types_chunks, problem_types_garden, problem_types_runners
from blizzard.wire.auth import (
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
    RunnerCredentialForbiddenProblem,
    SsoRefusedProblem,
)
from blizzard.wire.chunk import ChunkNotFoundProblem
from blizzard.wire.problem import (
    CLAIM_CONFLICT,
    CLAIM_TERMINAL,
    PROBLEM_TYPES,
    TENANT_NOT_FOUND,
    TENANT_REQUIRED,
    ProblemDetails,
)
from blizzard.wire.problem_registry import ALL_PROBLEM_TYPES
from blizzard.wire.problem_types_admin import ADMIN_PROBLEM_TYPES
from blizzard.wire.problem_types_chunks import CHUNKS_PROBLEM_TYPES
from blizzard.wire.problem_types_garden import GARDEN_PROBLEM_TYPES
from blizzard.wire.problem_types_runners import RUNNERS_PROBLEM_TYPES
from blizzard.wire.project import ProjectRef, ProjectRequiredBody, ProjectRequiredProblem
from blizzard.wire.release import ReleaseProblem, ReleaseRefusal
from blizzard.wire.route import (
    RouteClaimConflict,
    RouteClaimConflictProblem,
    RouteClaimDependencyDenial,
    RouteClaimDependencyProblem,
    RouteClaimIncompatibleDenial,
    RouteClaimIncompatibleProblem,
    RouteClaimTerminalDenial,
    RouteClaimTerminalProblem,
)
from blizzard.wire.runner import (
    RunnerIdentityProblem,
    RunnerIdentityRefusal,
    RunnerRetireInterruptedProblem,
    RunnerVersionProblem,
    RunnerVersionRefusal,
)
from blizzard.wire.tenant import TenantNotFoundProblem, TenantRequiredDetail, TenantRequiredProblem


def test_problem_registry_has_unique_stable_uris() -> None:
    expected = {
        "CLAIM_CONFLICT": "urn:blizzard:problem:claim-conflict",
        "CLAIM_TERMINAL": "urn:blizzard:problem:claim-terminal",
        "CLAIM_DEPENDENCY": "urn:blizzard:problem:claim-dependency",
        "CLAIM_INCOMPATIBLE": "urn:blizzard:problem:claim-incompatible",
        "CLAIM_PAUSED": "urn:blizzard:problem:claim-paused",
        "CLAIM_RUNNER_RETIRED": "urn:blizzard:problem:claim-runner-retired",
        "CLAIM_RUNNER_UNREGISTERED": "urn:blizzard:problem:claim-runner-unregistered",
        "CLAIM_RUNNER_VERSION_REFUSED": "urn:blizzard:problem:claim-runner-version-refused",
        "CLAIM_NOT_READY": "urn:blizzard:problem:claim-not-ready",
        "RUNNER_IDENTITY": "urn:blizzard:problem:runner-identity",
        "RUNNER_VERSION": "urn:blizzard:problem:runner-version",
        "RELEASE_REFUSAL": "urn:blizzard:problem:release-refusal",
        "TENANT_REQUIRED": "urn:blizzard:problem:tenant-required",
        "TENANT_NOT_FOUND": "urn:blizzard:problem:tenant-not-found",
        "TENANT_CREDENTIAL_MISMATCH": "urn:blizzard:problem:tenant-credential-mismatch",
        "AUTHENTICATION_REQUIRED": "urn:blizzard:problem:authentication-required",
        "AUTHENTICATION_REVOKED": "urn:blizzard:problem:authentication-revoked",
        "AUTHENTICATION_UNRESOLVED": "urn:blizzard:problem:authentication-unresolved",
        "PINNED_GRAPH_MISSING": "urn:blizzard:problem:pinned-graph-missing",
        "MALFORMED_CURSOR": "urn:blizzard:problem:malformed-cursor",
        "UNKNOWN_SERVED_PROJECT": "urn:blizzard:problem:unknown-served-project",
        "UNKNOWN_PROJECT": "urn:blizzard:problem:unknown-project",
        "CHUNK_NOT_FOUND": "urn:blizzard:problem:chunk-not-found",
        "CHUNK_NOT_PAUSABLE": "urn:blizzard:problem:chunk-not-pausable",
        "NO_CURRENT_NODE": "urn:blizzard:problem:no-current-node",
        "NO_RUN_CONTEXT": "urn:blizzard:problem:no-run-context",
        "NO_ANSWERED_PROPOSAL": "urn:blizzard:problem:no-answered-proposal",
        "FINDING_NOT_IN_CHUNK": "urn:blizzard:problem:finding-not-in-chunk",
        "QUESTION_NOT_FOUND": "urn:blizzard:problem:question-not-found",
        "RUNNER_NOT_FOUND": "urn:blizzard:problem:runner-not-found",
        "RUNNER_NOT_REGISTERED": "urn:blizzard:problem:runner-not-registered",
        "RUNNER_RETIRED": "urn:blizzard:problem:runner-retired",
        "HOST_RETIRED": "urn:blizzard:problem:host-retired",
        "RUNNER_OWNERSHIP": "urn:blizzard:problem:runner-ownership",
        "LEASE_OWNERSHIP": "urn:blizzard:problem:lease-ownership",
        "ROUTE_NOT_FOUND": "urn:blizzard:problem:route-not-found",
        "ROUTE_NOT_HELD": "urn:blizzard:problem:route-not-held",
        "ROUTE_TERMINAL": "urn:blizzard:problem:route-terminal",
        "SYSTEM_ARTIFACT_NOT_FOUND": "urn:blizzard:problem:system-artifact-not-found",
        "HOST_NAME_TAKEN": "urn:blizzard:problem:host-name-taken",
        "RUNNER_ALREADY_HOSTED": "urn:blizzard:problem:runner-already-hosted",
        "RUNNER_LIFECYCLE_CONFLICT": "urn:blizzard:problem:runner-lifecycle-conflict",
        "RUNNER_HOLDS_ROUTES": "urn:blizzard:problem:runner-holds-routes",
        "RUNNER_RETIRE_INTERRUPTED": "urn:blizzard:problem:runner-retire-interrupted",
        "PERMISSION_REQUIRED": "urn:blizzard:problem:permission-required",
        "MACHINE_CREDENTIAL_FORBIDDEN": "urn:blizzard:problem:machine-credential-forbidden",
        "RUNNER_CREDENTIAL_FORBIDDEN": "urn:blizzard:problem:runner-credential-forbidden",
        "IDP_DISABLED": "urn:blizzard:problem:idp-disabled",
        "FEDERATION_RESPONSE_MODE": "urn:blizzard:problem:federation-response-mode",
        "FEDERATION_CLIENT_REFUSED": "urn:blizzard:problem:federation-client-refused",
        "FEDERATION_NO_PROVIDER": "urn:blizzard:problem:federation-no-provider",
        "CLI_REDIRECT_REFUSED": "urn:blizzard:problem:cli-redirect-refused",
        "CLI_PKCE_REQUIRED": "urn:blizzard:problem:cli-pkce-required",
        "CLI_TOKEN_REFUSED": "urn:blizzard:problem:cli-token-refused",
        "LOGIN_DISABLED": "urn:blizzard:problem:login-disabled",
        "LOGIN_PROVIDER_UNKNOWN": "urn:blizzard:problem:login-provider-unknown",
        "LOGIN_THROTTLED": "urn:blizzard:problem:login-throttled",
        "LOGIN_FAILED": "urn:blizzard:problem:login-failed",
        "SSO_REFUSED": "urn:blizzard:problem:sso-refused",
        "PROJECT_REQUIRED": "urn:blizzard:problem:project-required",
        "HUB_STORE_UNAVAILABLE": "urn:blizzard:problem:hub-store-unavailable",
        "FLAG_OFF": "urn:blizzard:problem:flag-off",
        "INVARIANTS_IN_PROGRESS": "urn:blizzard:problem:invariants-in-progress",
        "SECRET_ALREADY_EXISTS": "urn:blizzard:problem:secret-already-exists",
        "SECRET_NOT_FOUND": "urn:blizzard:problem:secret-not-found",
        "DECISION_ALREADY_RESOLVED": "urn:blizzard:problem:decision-already-resolved",
        "QUESTION_ALREADY_ANSWERED": "urn:blizzard:problem:question-already-answered",
        "EGRESS_BACKFILL_FAILED": "urn:blizzard:problem:egress-backfill-failed",
        "TRACE_REPLAY_FAILED": "urn:blizzard:problem:trace-replay-failed",
        "REVISION_CONFLICT": "urn:blizzard:problem:revision-conflict",
        "POINTER_ALREADY_HELD": "urn:blizzard:problem:pointer-already-held",
        "DEPENDENT_NOT_EDITABLE": "urn:blizzard:problem:dependent-not-editable",
        "DEPENDENCY_WOULD_CLOSE_CYCLE": "urn:blizzard:problem:dependency-would-close-cycle",
        "PREREQUISITE_EPHEMERAL": "urn:blizzard:problem:prerequisite-ephemeral",
        "NO_STANDING_DEPENDENCY": "urn:blizzard:problem:no-standing-dependency",
        "CONFIG_APPLY_CONFLICT": "urn:blizzard:problem:config-apply-conflict",
        "CONFIG_MEDIA_TYPE_UNSUPPORTED": "urn:blizzard:problem:config-media-type-unsupported",
        "CONFIG_DOCUMENT_INVALID": "urn:blizzard:problem:config-document-invalid",
        "GRAPH_DEFINITION_INVALID": "urn:blizzard:problem:graph-definition-invalid",
    }
    constants = {name: value for name, value in vars(registry).items() if name.isupper() and isinstance(value, str)}
    assert constants == expected
    assert len(constants) == len(set(constants.values()))
    assert set(expected.values()) == PROBLEM_TYPES


@pytest.mark.parametrize(
    ("model", "uri"),
    [
        (RunnerCredentialForbiddenProblem, registry.RUNNER_CREDENTIAL_FORBIDDEN),
        (IdpDisabledProblem, registry.IDP_DISABLED),
        (FederationResponseModeProblem, registry.FEDERATION_RESPONSE_MODE),
        (FederationClientRefusedProblem, registry.FEDERATION_CLIENT_REFUSED),
        (FederationNoProviderProblem, registry.FEDERATION_NO_PROVIDER),
        (CliRedirectRefusedProblem, registry.CLI_REDIRECT_REFUSED),
        (CliPkceRequiredProblem, registry.CLI_PKCE_REQUIRED),
        (CliTokenRefusedProblem, registry.CLI_TOKEN_REFUSED),
        (LoginDisabledProblem, registry.LOGIN_DISABLED),
        (LoginProviderUnknownProblem, registry.LOGIN_PROVIDER_UNKNOWN),
        (LoginThrottledProblem, registry.LOGIN_THROTTLED),
    ],
)
def test_auth_problem_discriminators(model: type[ProblemDetails[str]], uri: str) -> None:
    body = {"type": uri, "title": "Refused", "detail": "refused", "instance": "/api/auth/authorize"}
    assert model.model_validate(body).problem_dump() == body
    assert model.model_json_schema()["properties"]["type"]["const"] == uri
    with pytest.raises(ValidationError):
        model.model_validate({**body, "type": registry.CLAIM_CONFLICT})


@pytest.mark.parametrize(
    ("model", "uri", "error"),
    [
        (LoginFailedProblem, registry.LOGIN_FAILED, "login_failed"),
        (SsoRefusedProblem, registry.SSO_REFUSED, "sso_refused"),
    ],
)
def test_provider_callback_preserves_legacy_error(model: type[ProblemDetails[str]], uri: str, error: str) -> None:
    body = {
        "type": uri,
        "title": "Refused",
        "detail": "refused",
        "instance": "/api/auth/provider/callback",
        "error": error,
    }
    assert model.model_validate(body).problem_dump() == body
    schema = model.model_json_schema()
    assert "error" in schema["required"]
    assert schema["properties"]["error"]["deprecated"] is True
    with pytest.raises(ValidationError):
        model.model_validate({**body, "error": "other"})


def test_partial_runner_retirement_has_distinct_problem_type() -> None:
    body = RunnerRetireInterruptedProblem(
        type=registry.RUNNER_RETIRE_INTERRUPTED,
        title="Retirement interrupted",
        detail="retirement interrupted",
        instance="/api/fleet/host/runners/r/retire",
        status=409,
    )
    assert body.problem_dump()["type"] == registry.RUNNER_RETIRE_INTERRUPTED
    assert body.model_json_schema()["properties"]["type"]["const"] == registry.RUNNER_RETIRE_INTERRUPTED


def test_pause_missing_chunk_precheck_and_race_share_semantics() -> None:
    # Both the precheck and ChunkNotFound under the pause lock emit this exact detail.
    body = ChunkNotFoundProblem(
        type=registry.CHUNK_NOT_FOUND,
        title="Chunk not found",
        detail="unknown chunk c",
        instance="/api/fleet/chunks/c/pause",
        status=404,
    )
    assert body.problem_dump()["detail"] == "unknown chunk c"


def test_nonterminal_claim_never_accepts_status() -> None:
    body = {
        "type": CLAIM_CONFLICT,
        "title": "Conflict",
        "detail": "chunk already claimed",
        "instance": "/routes",
        "chunk_id": "c",
        "held_by_runner_id": "r",
    }
    problem = RouteClaimConflictProblem.model_validate(body)
    assert "status" not in problem.model_dump()
    assert "status" not in problem.model_json_schema()["properties"]
    with pytest.raises(ValidationError):
        RouteClaimConflictProblem.model_validate({**body, "status": None})


@pytest.mark.parametrize(
    ("model", "extra"),
    [
        (RouteClaimConflictProblem, {"held_by_runner_id": "r"}),
        (RouteClaimDependencyProblem, {"prerequisite_chunk_id": "p"}),
        (RouteClaimIncompatibleProblem, {"incompatible_runner_id": "r"}),
    ],
)
def test_statusless_claims_allow_extensions_but_reject_status(
    model: type[RouteClaimConflictProblem] | type[RouteClaimDependencyProblem] | type[RouteClaimIncompatibleProblem],
    extra: dict[str, str],
) -> None:
    uri = model.model_json_schema()["properties"]["type"]["const"]
    body = {"type": uri, "title": "Denied", "detail": "denied", "instance": "/", "chunk_id": "c", **extra}
    assert model.model_validate({**body, "future": "value"}).problem_dump()["future"] == "value"
    for status in (None, 409, "done"):
        with pytest.raises(ValidationError):
            model.model_validate({**body, "status": status})
    assert model.model_json_schema(mode="serialization")["not"] == {"required": ["status"]}

    problem = model.model_validate({**body, "future": "value"})
    problem.status = 409  # pyright: ignore[reportAttributeAccessIssue]  # exercise extra assignment
    copied = problem.model_copy(update={"status": "done", "another": "extension"})
    app = FastAPI()

    @app.get("/", response_model=model)
    def get_problem() -> object:
        return copied

    for serialized in (
        problem.model_dump(),
        problem.problem_dump(),
        copied.model_dump(mode="json"),
        TypeAdapter(model).dump_python(copied),
        TypeAdapter(model).dump_json(copied),
        copied.model_dump_json(),
        TestClient(app).get("/").json(),
    ):
        if isinstance(serialized, (str, bytes)):
            serialized = json.loads(serialized)
        assert "status" not in serialized
        assert serialized["future"] == "value"
    assert copied.model_dump()["another"] == "extension"


def test_openapi_response_uses_nonnullable_optional_status_and_sent_defaults() -> None:
    app = FastAPI()

    @app.get("/problem", response_model=RunnerIdentityProblem)
    def problem() -> RunnerIdentityProblem:
        return RunnerIdentityProblem.model_validate(
            {
                "type": registry.RUNNER_IDENTITY,
                "title": "Denied",
                "detail": "denied",
                "instance": "/",
                "reason": "missing",
            }
        )

    schema = app.openapi()["components"]["schemas"]["RunnerIdentityProblem"]
    assert "runner_id" in schema["required"]
    assert "status" not in schema["required"]
    assert {variant.get("type") for variant in schema["properties"]["status"]["anyOf"]} == {"integer"}
    assert TestClient(app).get("/problem").json() == {
        "type": registry.RUNNER_IDENTITY,
        "title": "Denied",
        "detail": "denied",
        "instance": "/",
        "reason": "missing",
        "runner_id": None,
    }


@pytest.mark.parametrize("status", [None, 401])
def test_normal_problem_serializers_preserve_optional_status(status: int | None) -> None:
    problem = RunnerIdentityProblem.model_validate(
        {
            "type": registry.RUNNER_IDENTITY,
            "title": "Denied",
            "detail": "denied",
            "instance": "/",
            "reason": "missing",
            "status": status,
            "future": None,
        }
    )
    expected: dict[str, object] = {
        "type": registry.RUNNER_IDENTITY,
        "title": "Denied",
        "detail": "denied",
        "instance": "/",
        "reason": "missing",
        "runner_id": None,
        "future": None,
    }
    if status is not None:
        expected["status"] = status
    assert problem.model_dump() == expected
    assert json.loads(problem.model_dump_json()) == expected
    assert problem.problem_dump() == expected
    assert TypeAdapter(RunnerIdentityProblem).dump_python(problem, mode="json") == expected


def test_terminal_claim_keeps_string_status_and_deprecated_extensions() -> None:
    problem = RouteClaimTerminalProblem(
        type=CLAIM_TERMINAL, title="Denied", detail="chunk is terminal", instance="/routes", chunk_id="c", status="done"
    )
    assert problem.model_dump()["status"] == "done"
    schema = RouteClaimTerminalProblem.model_json_schema()
    assert schema["properties"]["status"]["deprecated"] is True
    assert {"type", "title", "detail", "instance", "status"} <= set(schema["required"])


def test_tenant_required_preserves_object_detail_and_required_members() -> None:
    problem = TenantRequiredProblem(
        type=TENANT_REQUIRED,
        title="Tenant required",
        detail=TenantRequiredDetail(code="tenant_required", message="name the tenant to act in"),
        instance="/api/hosts",
        status=409,
    )
    assert problem.model_dump(exclude_none=True)["detail"] == {
        "code": "tenant_required",
        "message": "name the tenant to act in",
    }
    schema = TenantRequiredProblem.model_json_schema()
    assert {"type", "title", "detail", "instance"} <= set(schema["required"])
    assert "status" not in schema["required"]
    with pytest.raises(ValidationError):
        TenantRequiredProblem.model_validate({**problem.model_dump(), "detail": "tenant_required"})


def test_tenant_not_found_preserves_exact_detail() -> None:
    problem = TenantNotFoundProblem(
        type=TENANT_NOT_FOUND, title="Not found", detail="no such tenant", instance="/api/hosts", status=404
    )
    assert problem.model_dump(exclude_none=True)["detail"] == "no such tenant"
    with pytest.raises(ValidationError):
        TenantNotFoundProblem.model_validate({**problem.model_dump(), "detail": "unknown tenant"})


def test_project_required_detail_keeps_the_released_choice_shape() -> None:
    problem = ProjectRequiredProblem(
        type="urn:blizzard:problem:project-required",
        title="Project required",
        detail=ProjectRequiredBody(
            message="choose a project", projects=[ProjectRef(project_id="p", slug="s", name="n")]
        ),
        instance="/api/hosts",
        status=422,
    )
    assert problem.model_dump(exclude_none=True)["detail"] == {
        "code": "project_required",
        "message": "choose a project",
        "projects": [{"project_id": "p", "slug": "s", "name": "n"}],
    }
    assert set(ProjectRequiredBody.model_json_schema(mode="serialization")["required"]) == {
        "code",
        "message",
        "projects",
    }


@pytest.mark.parametrize(
    ("model", "body"),
    [
        (
            RouteClaimIncompatibleProblem,
            {"type": "urn:blizzard:problem:claim-incompatible", "chunk_id": "c", "incompatible_runner_id": "r"},
        ),
        (
            RunnerVersionProblem,
            {
                "type": "urn:blizzard:problem:runner-version",
                "status": 409,
                "reason": "too_old",
                "hub_version": "0.4.0",
                "floor": "0.2",
            },
        ),
        (
            RunnerIdentityProblem,
            {"type": "urn:blizzard:problem:runner-identity", "status": 401, "reason": "missing", "runner_id": None},
        ),
        (
            TenantRequiredProblem,
            {"type": TENANT_REQUIRED, "status": 409, "detail": {"code": "tenant_required", "message": "choose"}},
        ),
    ],
)
def test_serialization_schema_requires_every_sent_field(
    model: type[RouteClaimIncompatibleProblem]
    | type[RunnerVersionProblem]
    | type[RunnerIdentityProblem]
    | type[TenantRequiredProblem],
    body: dict[str, object],
) -> None:
    data = {"title": "Refused", "detail": "refused", "instance": "/api/fleet", **body}
    problem = model.model_validate(data)
    serialized = problem.problem_dump()
    schema = model.model_json_schema(mode="serialization")
    assert set(schema["required"]) == set(serialized) - ({"status"} if "status" in serialized else set())
    if model is RouteClaimIncompatibleProblem:
        assert "status" not in serialized
        assert schema["properties"]["reason"]["deprecated"] is True
    if model is RunnerVersionProblem:
        assert serialized["runner_version"] is None


@pytest.mark.parametrize(
    ("problem", "legacy"),
    [
        (RouteClaimConflictProblem, RouteClaimConflict),
        (RouteClaimTerminalProblem, RouteClaimTerminalDenial),
        (RouteClaimDependencyProblem, RouteClaimDependencyDenial),
        (RouteClaimIncompatibleProblem, RouteClaimIncompatibleDenial),
        (RunnerIdentityProblem, RunnerIdentityRefusal),
        (RunnerVersionProblem, RunnerVersionRefusal),
        (ReleaseProblem, ReleaseRefusal),
    ],
)
def test_problem_extends_existing_wire_model(problem: type, legacy: type) -> None:
    assert issubclass(problem, legacy)


def test_optional_status_and_identity_null_are_independent() -> None:
    body = {
        "type": "urn:blizzard:problem:runner-identity",
        "title": "Denied",
        "detail": "missing",
        "instance": "/",
        "reason": "missing",
    }
    problem = RunnerIdentityProblem.model_validate(body)
    serialized = problem.problem_dump()
    assert serialized["runner_id"] is None
    assert "status" not in serialized
    assert "status" not in RunnerIdentityProblem.model_json_schema(mode="serialization")["required"]
    assert problem.model_dump(exclude_unset=True) == body
    assert RunnerIdentityProblem.model_validate({**body, "status": 401}).status == 401


def test_legacy_defaults_and_required_fields_survive() -> None:
    assert RouteClaimConflict(chunk_id="c", held_by_runner_id="r").detail == "chunk already claimed"
    schema = RouteClaimIncompatibleProblem.model_json_schema(mode="serialization")
    assert schema["properties"]["reason"]["deprecated"] is True
    assert "reason" in schema["required"]
    assert RunnerIdentityProblem.model_json_schema()["properties"]["runner_id"]["deprecated"] is True
    assert ReleaseProblem.model_json_schema()["properties"]["reason"]["deprecated"] is True


def test_every_declared_type_constant_is_in_the_registry() -> None:
    modules = (registry, problem_types_admin, problem_types_chunks, problem_types_garden, problem_types_runners)
    declared = {
        value
        for module in modules
        for value in vars(module).values()
        if isinstance(value, str) and value.startswith("urn:blizzard:problem:")
    }

    assert declared == set(ALL_PROBLEM_TYPES)


def test_every_problem_model_literal_is_registered() -> None:
    declared: set[str] = set()
    for module_info in pkgutil.walk_packages(wire.__path__, wire.__name__ + "."):
        module = importlib.import_module(module_info.name)
        for _, model in inspect.getmembers(module, inspect.isclass):
            if model.__module__ != module.__name__ or not issubclass(model, BaseModel):
                continue
            field = model.model_fields.get("type")
            if field is not None:
                declared.update(
                    value
                    for value in get_args(field.annotation)
                    if isinstance(value, str) and value.startswith("urn:blizzard:problem:")
                )
    assert declared <= ALL_PROBLEM_TYPES


def test_every_family_type_is_registered_once_and_named_by_its_concept() -> None:
    families = (
        ADMIN_PROBLEM_TYPES,
        CHUNKS_PROBLEM_TYPES,
        GARDEN_PROBLEM_TYPES,
        RUNNERS_PROBLEM_TYPES,
    )
    everything = [uri for group in (PROBLEM_TYPES, *families) for uri in group]

    assert len(everything) == len(set(everything)) == len(ALL_PROBLEM_TYPES)
    assert all(uri.startswith("urn:blizzard:problem:") for uri in ALL_PROBLEM_TYPES)

"""Problem serialization preserves the legacy claim status distinction."""

import ast
import importlib
import inspect
import json
import pkgutil
from collections.abc import Iterator
from types import ModuleType
from typing import Final, Literal, get_args, get_origin

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import BaseModel, TypeAdapter, ValidationError

import blizzard.wire as wire
import blizzard.wire.problem_registry as registry
from blizzard.wire.auth import (
    CLI_PKCE_REQUIRED,
    CLI_REDIRECT_REFUSED,
    CLI_TOKEN_REFUSED,
    FEDERATION_CLIENT_REFUSED,
    FEDERATION_NO_PROVIDER,
    FEDERATION_RESPONSE_MODE,
    IDP_DISABLED,
    LOGIN_DISABLED,
    LOGIN_FAILED,
    LOGIN_PROVIDER_UNKNOWN,
    LOGIN_THROTTLED,
    RUNNER_CREDENTIAL_FORBIDDEN,
    SSO_REFUSED,
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
from blizzard.wire.chunk import CHUNK_NOT_FOUND, ChunkNotFoundProblem
from blizzard.wire.config import CONFIG_DOCUMENT_INVALID, CONFIG_FIELD_INVALID
from blizzard.wire.flags import FLAG_OFF
from blizzard.wire.forge import FORGE_IN_USE, FORGE_ORIGIN_TAKEN, FORGE_REVISION_CONFLICT, FORGE_SLUG_TAKEN
from blizzard.wire.problem import (
    ProblemDetails,
)
from blizzard.wire.problem_registry import ALL_PROBLEM_TYPES
from blizzard.wire.project import (
    LANDING_ORDER_CONFLICT,
    PROJECT_REQUIRED,
    UNKNOWN_PROJECT,
    ProjectRef,
    ProjectRequiredBody,
    ProjectRequiredProblem,
)
from blizzard.wire.release import ReleaseProblem, ReleaseRefusal
from blizzard.wire.route import (
    CLAIM_CONFLICT,
    CLAIM_TERMINAL,
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
    RUNNER_IDENTITY,
    RUNNER_RETIRE_INTERRUPTED,
    RunnerIdentityProblem,
    RunnerIdentityRefusal,
    RunnerRetireInterruptedProblem,
    RunnerVersionProblem,
    RunnerVersionRefusal,
)
from blizzard.wire.secret import SECRET_IN_USE
from blizzard.wire.tenant import (
    LAST_TENANT,
    TENANT_NOT_FOUND,
    TENANT_REQUIRED,
    TenantNotFoundProblem,
    TenantRequiredDetail,
    TenantRequiredProblem,
)
from blizzard.wire.work_source import UNKNOWN_WORK_SOURCE


def _wire_modules() -> Iterator[ModuleType]:
    """Census the package independently of the registry integrator's imports."""
    for info in pkgutil.walk_packages(wire.__path__, wire.__name__ + "."):
        yield importlib.import_module(info.name)


def _local_problem_constants(module: ModuleType) -> dict[str, str]:
    tree = ast.parse(inspect.getsource(module))
    return {
        node.target.id: node.value.value
        for node in tree.body
        if isinstance(node, ast.AnnAssign)
        and isinstance(node.target, ast.Name)
        and _is_final(node.annotation)
        and isinstance(node.value, ast.Constant)
        and isinstance(node.value.value, str)
        and node.value.value.startswith("urn:blizzard:problem:")
    }


def _is_final(annotation: ast.expr) -> bool:
    if isinstance(annotation, ast.Subscript):
        annotation = annotation.value
    return (isinstance(annotation, ast.Name) and annotation.id == "Final") or (
        isinstance(annotation, ast.Attribute)
        and isinstance(annotation.value, ast.Name)
        and annotation.value.id == "typing"
        and annotation.attr == "Final"
    )


def test_problem_registry_has_unique_stable_uris() -> None:
    # Captured from the five contributions at the pre-colocation base.
    expected = {
        "ABSORBER_NOT_LIVE": "urn:blizzard:problem:absorber-not-live",
        "AUTHENTICATION_REQUIRED": "urn:blizzard:problem:authentication-required",
        "AUTHENTICATION_REVOKED": "urn:blizzard:problem:authentication-revoked",
        "AUTHENTICATION_UNRESOLVED": "urn:blizzard:problem:authentication-unresolved",
        "BLANK_EDIT_VALUE": "urn:blizzard:problem:blank-edit-value",
        "CADENCE_ADMISSION_REFUSED": "urn:blizzard:problem:cadence-admission-refused",
        "CADENCE_NOT_FOUND": "urn:blizzard:problem:cadence-not-found",
        "CADENCE_SLUG_TAKEN": "urn:blizzard:problem:cadence-slug-taken",
        "CHUNKS_IN_DIFFERENT_PROJECTS": "urn:blizzard:problem:chunks-in-different-projects",
        "CHUNK_ALREADY_MOVED": "urn:blizzard:problem:chunk-already-moved",
        "CHUNK_HAS_DEPENDENTS": "urn:blizzard:problem:chunk-has-dependents",
        "CHUNK_NOT_DELETABLE": "urn:blizzard:problem:chunk-not-deletable",
        "CHUNK_NOT_DEMOTABLE": "urn:blizzard:problem:chunk-not-demotable",
        "CHUNK_NOT_EDITABLE": "urn:blizzard:problem:chunk-not-editable",
        "CHUNK_NOT_ESCALATED": "urn:blizzard:problem:chunk-not-escalated",
        "CHUNK_NOT_FOUND": "urn:blizzard:problem:chunk-not-found",
        "CHUNK_NOT_GROUPABLE": "urn:blizzard:problem:chunk-not-groupable",
        "CHUNK_NOT_PAUSABLE": "urn:blizzard:problem:chunk-not-pausable",
        "CHUNK_NOT_PROMOTABLE": "urn:blizzard:problem:chunk-not-promotable",
        "CHUNK_NOT_RESTARTABLE": "urn:blizzard:problem:chunk-not-restartable",
        "CHUNK_NOT_ROUTED": "urn:blizzard:problem:chunk-not-routed",
        "CHUNK_NOT_STOPPABLE": "urn:blizzard:problem:chunk-not-stoppable",
        "CLAIM_CONFLICT": "urn:blizzard:problem:claim-conflict",
        "CLAIM_DEPENDENCY": "urn:blizzard:problem:claim-dependency",
        "CLAIM_INCOMPATIBLE": "urn:blizzard:problem:claim-incompatible",
        "CLAIM_NOT_READY": "urn:blizzard:problem:claim-not-ready",
        "CLAIM_PAUSED": "urn:blizzard:problem:claim-paused",
        "CLAIM_RUNNER_RETIRED": "urn:blizzard:problem:claim-runner-retired",
        "CLAIM_RUNNER_UNREGISTERED": "urn:blizzard:problem:claim-runner-unregistered",
        "CLAIM_RUNNER_VERSION_REFUSED": "urn:blizzard:problem:claim-runner-version-refused",
        "CLAIM_TERMINAL": "urn:blizzard:problem:claim-terminal",
        "CLI_PKCE_REQUIRED": "urn:blizzard:problem:cli-pkce-required",
        "CLI_REDIRECT_REFUSED": "urn:blizzard:problem:cli-redirect-refused",
        "CLI_TOKEN_REFUSED": "urn:blizzard:problem:cli-token-refused",
        "CONFIG_APPLY_CONFLICT": "urn:blizzard:problem:config-apply-conflict",
        "CONFIG_DOCUMENT_INVALID": "urn:blizzard:problem:config-document-invalid",
        "CONFIG_FIELD_INVALID": "urn:blizzard:problem:config-field-invalid",
        "CONFIG_MEDIA_TYPE_UNSUPPORTED": "urn:blizzard:problem:config-media-type-unsupported",
        "CONFIG_SCHEMA_NOT_FOUND": "urn:blizzard:problem:config-schema-not-found",
        "CONTRADICTORY_PLACEMENT": "urn:blizzard:problem:contradictory-placement",
        "CURRENT_NODE_NOT_ON_GRAPH": "urn:blizzard:problem:current-node-not-on-graph",
        "DECISION_ALREADY_RESOLVED": "urn:blizzard:problem:decision-already-resolved",
        "DECISION_CLOSED": "urn:blizzard:problem:decision-closed",
        "DECISION_NOT_FOUND": "urn:blizzard:problem:decision-not-found",
        "DEFAULT_GRAPH_RETIRED": "urn:blizzard:problem:default-graph-retired",
        "DEPENDENCY_WOULD_CLOSE_CYCLE": "urn:blizzard:problem:dependency-would-close-cycle",
        "DEPENDENT_NOT_EDITABLE": "urn:blizzard:problem:dependent-not-editable",
        "DUPLICATE_CAPABILITY_HARNESS": "urn:blizzard:problem:duplicate-capability-harness",
        "DUPLICATE_FINDING": "urn:blizzard:problem:duplicate-finding",
        "DUPLICATE_REORDER_IDS": "urn:blizzard:problem:duplicate-reorder-ids",
        "EGRESS_BACKFILL_FAILED": "urn:blizzard:problem:egress-backfill-failed",
        "EGRESS_BACKFILL_UNAVAILABLE": "urn:blizzard:problem:egress-backfill-unavailable",
        "EGRESS_BACKFILL_WINDOW_REFUSED": "urn:blizzard:problem:egress-backfill-window-refused",
        "EGRESS_RESET_REFUSED": "urn:blizzard:problem:egress-reset-refused",
        "EGRESS_RESET_UNAVAILABLE": "urn:blizzard:problem:egress-reset-unavailable",
        "FEDERATION_CLIENT_REFUSED": "urn:blizzard:problem:federation-client-refused",
        "FEDERATION_NO_PROVIDER": "urn:blizzard:problem:federation-no-provider",
        "FEDERATION_RESPONSE_MODE": "urn:blizzard:problem:federation-response-mode",
        "FINDING_NOTE_REQUIRED": "urn:blizzard:problem:finding-note-required",
        "FINDING_NOT_FOUND": "urn:blizzard:problem:finding-not-found",
        "FINDING_NOT_IN_CHUNK": "urn:blizzard:problem:finding-not-in-chunk",
        "FINDING_SUPERSEDES_ITSELF": "urn:blizzard:problem:finding-supersedes-itself",
        "FINDING_TRANSITION_REFUSED": "urn:blizzard:problem:finding-transition-refused",
        "FINDING_WRITE_CONTENDED": "urn:blizzard:problem:finding-write-contended",
        "FLAG_OFF": "urn:blizzard:problem:flag-off",
        "FLOW_NOT_FOUND": "urn:blizzard:problem:flow-not-found",
        "FOLD_WOULD_CLOSE_CYCLE": "urn:blizzard:problem:fold-would-close-cycle",
        "FORGE_IN_USE": "urn:blizzard:problem:forge-in-use",
        "FORGE_NOT_FOUND": "urn:blizzard:problem:forge-not-found",
        "FORGE_ORIGIN_TAKEN": "urn:blizzard:problem:forge-origin-taken",
        "FORGE_REVISION_CONFLICT": "urn:blizzard:problem:forge-revision-conflict",
        "FORGE_SLUG_TAKEN": "urn:blizzard:problem:forge-slug-taken",
        "GARDEN_PROPOSAL_ALREADY_CLOSED": "urn:blizzard:problem:garden-proposal-already-closed",
        "GARDEN_PROPOSAL_INVALID": "urn:blizzard:problem:garden-proposal-invalid",
        "GARDEN_PROPOSAL_NOT_FOUND": "urn:blizzard:problem:garden-proposal-not-found",
        "GARDEN_WINDOW_INVALID": "urn:blizzard:problem:garden-window-invalid",
        "GRAPH_DEFINITION_INVALID": "urn:blizzard:problem:graph-definition-invalid",
        "GRAPH_NOT_FOUND": "urn:blizzard:problem:graph-not-found",
        "HOST_HOLDS_ROUTES": "urn:blizzard:problem:host-holds-routes",
        "HOST_LIFECYCLE_CONFLICT": "urn:blizzard:problem:host-lifecycle-conflict",
        "HOST_NAME_TAKEN": "urn:blizzard:problem:host-name-taken",
        "HOST_NOT_ENROLLED": "urn:blizzard:problem:host-not-enrolled",
        "HOST_NOT_FOUND": "urn:blizzard:problem:host-not-found",
        "HOST_RETIRED": "urn:blizzard:problem:host-retired",
        "HOST_RETIREMENT_INTERRUPTED": "urn:blizzard:problem:host-retirement-interrupted",
        "HOST_RETIREMENT_UNFINISHED": "urn:blizzard:problem:host-retirement-unfinished",
        "HUB_STORE_UNAVAILABLE": "urn:blizzard:problem:hub-store-unavailable",
        "IDP_DISABLED": "urn:blizzard:problem:idp-disabled",
        "INGEST_EMPTY": "urn:blizzard:problem:ingest-empty",
        "INGEST_ITEM_PROJECT_MISMATCH": "urn:blizzard:problem:ingest-item-project-mismatch",
        "INGEST_TOKEN_UNCLAIMED": "urn:blizzard:problem:ingest-token-unclaimed",
        "INVALID_HARNESSES": "urn:blizzard:problem:invalid-harnesses",
        "INVARIANTS_IN_PROGRESS": "urn:blizzard:problem:invariants-in-progress",
        "INVITATION_ALREADY_RESOLVED": "urn:blizzard:problem:invitation-already-resolved",
        "INVITATION_NOT_FOUND": "urn:blizzard:problem:invitation-not-found",
        "LANDING_ORDER_CONFLICT": "urn:blizzard:problem:landing-order-conflict",
        "LAST_ADMIN_KEPT": "urn:blizzard:problem:last-admin-kept",
        "LAST_HUB_ADMIN": "urn:blizzard:problem:last-hub-admin",
        "LAST_TENANT": "urn:blizzard:problem:last-tenant",
        "LEASE_OWNERSHIP": "urn:blizzard:problem:lease-ownership",
        "LOGIN_DISABLED": "urn:blizzard:problem:login-disabled",
        "LOGIN_FAILED": "urn:blizzard:problem:login-failed",
        "LOGIN_PROVIDER_UNKNOWN": "urn:blizzard:problem:login-provider-unknown",
        "LOGIN_THROTTLED": "urn:blizzard:problem:login-throttled",
        "MACHINE_CREDENTIAL_FORBIDDEN": "urn:blizzard:problem:machine-credential-forbidden",
        "MALFORMED_CURSOR": "urn:blizzard:problem:malformed-cursor",
        "MEMBERSHIP_CHANGE_REFUSED": "urn:blizzard:problem:membership-change-refused",
        "MEMBER_NOT_FOUND": "urn:blizzard:problem:member-not-found",
        "MIGRATION_TARGET_IS_CURRENT_PIN": "urn:blizzard:problem:migration-target-is-current-pin",
        "NODE_STEP_NOT_FOUND": "urn:blizzard:problem:node-step-not-found",
        "NODE_NOT_ON_GRAPH": "urn:blizzard:problem:node-not-on-graph",
        "NOT_A_DECISION_CHOICE": "urn:blizzard:problem:not-a-decision-choice",
        "NOT_IN_LIST": "urn:blizzard:problem:not-in-list",
        "NO_ANSWERED_PROPOSAL": "urn:blizzard:problem:no-answered-proposal",
        "NO_CURRENT_NODE": "urn:blizzard:problem:no-current-node",
        "NO_RUN_CONTEXT": "urn:blizzard:problem:no-run-context",
        "NO_STANDING_DEPENDENCY": "urn:blizzard:problem:no-standing-dependency",
        "PERMISSION_REQUIRED": "urn:blizzard:problem:permission-required",
        "PINNED_GRAPH_MISSING": "urn:blizzard:problem:pinned-graph-missing",
        "POINTER_ALREADY_HELD": "urn:blizzard:problem:pointer-already-held",
        "PREREQUISITE_EPHEMERAL": "urn:blizzard:problem:prerequisite-ephemeral",
        "PROJECT_LINK_NOT_FOUND": "urn:blizzard:problem:project-link-not-found",
        "PROJECT_NOT_FOUND": "urn:blizzard:problem:project-not-found",
        "PROJECT_REQUIRED": "urn:blizzard:problem:project-required",
        "PROJECT_SLUG_TAKEN": "urn:blizzard:problem:project-slug-taken",
        "QUESTION_ALREADY_ANSWERED": "urn:blizzard:problem:question-already-answered",
        "QUESTION_CLOSED": "urn:blizzard:problem:question-closed",
        "QUESTION_NOT_FOUND": "urn:blizzard:problem:question-not-found",
        "QUESTION_WRITE_FENCED": "urn:blizzard:problem:question-write-fenced",
        "RELEASE_REFUSAL": "urn:blizzard:problem:release-refusal",
        "REPOSITORY_COORDINATE_TAKEN": "urn:blizzard:problem:repository-coordinate-taken",
        "REPOSITORY_NOT_FOUND": "urn:blizzard:problem:repository-not-found",
        "REPOSITORY_SLUG_TAKEN": "urn:blizzard:problem:repository-slug-taken",
        "RESTART_GRAPH_PIN_CHANGED": "urn:blizzard:problem:restart-graph-pin-changed",
        "REVISION_CONFLICT": "urn:blizzard:problem:revision-conflict",
        "RE_DERIVE_SCOPE_REFUSED": "urn:blizzard:problem:re-derive-scope-refused",
        "ROLE_NOT_ASSIGNABLE": "urn:blizzard:problem:role-not-assignable",
        "ROUTE_NOT_FOUND": "urn:blizzard:problem:route-not-found",
        "ROUTE_NOT_HELD": "urn:blizzard:problem:route-not-held",
        "ROUTE_TERMINAL": "urn:blizzard:problem:route-terminal",
        "ROUTINE_DEFINITION_INVALID": "urn:blizzard:problem:routine-definition-invalid",
        "ROUTINE_NOT_FOUND": "urn:blizzard:problem:routine-not-found",
        "ROUTINE_NOT_RUNNABLE": "urn:blizzard:problem:routine-not-runnable",
        "ROUTINE_SCOPE_NOT_RELATED": "urn:blizzard:problem:routine-scope-not-related",
        "RUNNER_ALREADY_HOSTED": "urn:blizzard:problem:runner-already-hosted",
        "RUNNER_CREDENTIAL_FORBIDDEN": "urn:blizzard:problem:runner-credential-forbidden",
        "RUNNER_HOLDS_ROUTES": "urn:blizzard:problem:runner-holds-routes",
        "RUNNER_IDENTITY": "urn:blizzard:problem:runner-identity",
        "RUNNER_LIFECYCLE_CONFLICT": "urn:blizzard:problem:runner-lifecycle-conflict",
        "RUNNER_NOT_ENROLLED": "urn:blizzard:problem:runner-not-enrolled",
        "RUNNER_NOT_FOUND": "urn:blizzard:problem:runner-not-found",
        "RUNNER_NOT_REGISTERED": "urn:blizzard:problem:runner-not-registered",
        "RUNNER_NOT_RETIRED": "urn:blizzard:problem:runner-not-retired",
        "RUNNER_OWNERSHIP": "urn:blizzard:problem:runner-ownership",
        "RUNNER_REINSTATE_INTERRUPTED": "urn:blizzard:problem:runner-reinstate-interrupted",
        "RUNNER_RETIRED": "urn:blizzard:problem:runner-retired",
        "RUNNER_RETIRE_INTERRUPTED": "urn:blizzard:problem:runner-retire-interrupted",
        "RUNNER_VERSION": "urn:blizzard:problem:runner-version",
        "RUN_NOT_FOUND": "urn:blizzard:problem:run-not-found",
        "SCOPE_NOT_FOUND": "urn:blizzard:problem:scope-not-found",
        "SCOPE_SLUG_INVALID": "urn:blizzard:problem:scope-slug-invalid",
        "SECRET_ALREADY_EXISTS": "urn:blizzard:problem:secret-already-exists",
        "SECRET_IN_USE": "urn:blizzard:problem:secret-in-use",
        "SECRET_NAME_INVALID": "urn:blizzard:problem:secret-name-invalid",
        "SECRET_NOT_FOUND": "urn:blizzard:problem:secret-not-found",
        "SECRET_RETIRED": "urn:blizzard:problem:secret-retired",
        "SECRET_VALUE_BLANK": "urn:blizzard:problem:secret-value-blank",
        "SELF_ANCHORED_MOVE": "urn:blizzard:problem:self-anchored-move",
        "SPEND_INSTANT_INVALID": "urn:blizzard:problem:spend-instant-invalid",
        "SSO_REFUSED": "urn:blizzard:problem:sso-refused",
        "SUBMISSION_NOT_FOUND": "urn:blizzard:problem:submission-not-found",
        "SWEEP_NOT_FOUND": "urn:blizzard:problem:sweep-not-found",
        "SWEEP_SERVES_NO_TENANT": "urn:blizzard:problem:sweep-serves-no-tenant",
        "SYSTEM_ARTIFACT_NOT_FOUND": "urn:blizzard:problem:system-artifact-not-found",
        "TARGET_GRAPH_RETIRED": "urn:blizzard:problem:target-graph-retired",
        "TENANT_CREDENTIAL_MISMATCH": "urn:blizzard:problem:tenant-credential-mismatch",
        "TENANT_DELETED": "urn:blizzard:problem:tenant-deleted",
        "TENANT_NAME_REFUSED": "urn:blizzard:problem:tenant-name-refused",
        "TENANT_NOT_FOUND": "urn:blizzard:problem:tenant-not-found",
        "TENANT_REQUIRED": "urn:blizzard:problem:tenant-required",
        "TRACE_REPLAY_FAILED": "urn:blizzard:problem:trace-replay-failed",
        "TRACE_REPLAY_UNAVAILABLE": "urn:blizzard:problem:trace-replay-unavailable",
        "TRACE_REPLAY_WINDOW_REFUSED": "urn:blizzard:problem:trace-replay-window-refused",
        "TRANSCRIPT_SEGMENT_NOT_FOUND": "urn:blizzard:problem:transcript-segment-not-found",
        "UNKNOWN_PROJECT": "urn:blizzard:problem:unknown-project",
        "UNKNOWN_ROLE": "urn:blizzard:problem:unknown-role",
        "UNKNOWN_SERVED_PROJECT": "urn:blizzard:problem:unknown-served-project",
        "UNKNOWN_WORK_SOURCE": "urn:blizzard:problem:unknown-work-source",
        "URGENCY_NOT_FOUND": "urn:blizzard:problem:urgency-not-found",
        "URGENCY_ICON_NOT_FOUND": "urn:blizzard:problem:urgency-icon-not-found",
        "URGENCY_IS_DEFAULT": "urn:blizzard:problem:urgency-is-default",
        "URGENCY_IN_USE": "urn:blizzard:problem:urgency-in-use",
        "USER_NOT_FOUND": "urn:blizzard:problem:user-not-found",
        "WORK_ITEMS_CHUNK_IDS_REQUIRED": "urn:blizzard:problem:work-items-chunk-ids-required",
        "WORK_ITEM_FIELD_BLANK": "urn:blizzard:problem:work-item-field-blank",
        "WORK_ITEM_HELD_BY_DEPENDENTS": "urn:blizzard:problem:work-item-held-by-dependents",
        "WORK_ITEM_HELD_BY_LIVE_CHUNK": "urn:blizzard:problem:work-item-held-by-live-chunk",
        "WORK_ITEM_NOT_EDITABLE": "urn:blizzard:problem:work-item-not-editable",
        "WORK_ITEM_NOT_FOUND": "urn:blizzard:problem:work-item-not-found",
        "WORK_SOURCE_LOCATOR_TAKEN": "urn:blizzard:problem:work-source-locator-taken",
        "WORK_SOURCE_NOT_FOUND": "urn:blizzard:problem:work-source-not-found",
        "WORK_SOURCE_NO_EDITOR": "urn:blizzard:problem:work-source-no-editor",
        "WORK_SOURCE_SLUG_TAKEN": "urn:blizzard:problem:work-source-slug-taken",
    }
    declarations = [
        (name, value) for module in _wire_modules() for name, value in _local_problem_constants(module).items()
    ]
    assert len(declarations) == len(dict(declarations)), "problem constant names must be unique"
    assert dict(declarations) == expected
    assert len(declarations) == len({value for _, value in declarations})
    assert set(expected.values()) == ALL_PROBLEM_TYPES


@pytest.mark.parametrize(
    ("model", "uri"),
    [
        (RunnerCredentialForbiddenProblem, RUNNER_CREDENTIAL_FORBIDDEN),
        (IdpDisabledProblem, IDP_DISABLED),
        (FederationResponseModeProblem, FEDERATION_RESPONSE_MODE),
        (FederationClientRefusedProblem, FEDERATION_CLIENT_REFUSED),
        (FederationNoProviderProblem, FEDERATION_NO_PROVIDER),
        (CliRedirectRefusedProblem, CLI_REDIRECT_REFUSED),
        (CliPkceRequiredProblem, CLI_PKCE_REQUIRED),
        (CliTokenRefusedProblem, CLI_TOKEN_REFUSED),
        (LoginDisabledProblem, LOGIN_DISABLED),
        (LoginProviderUnknownProblem, LOGIN_PROVIDER_UNKNOWN),
        (LoginThrottledProblem, LOGIN_THROTTLED),
    ],
)
def test_auth_problem_discriminators(model: type[ProblemDetails[str]], uri: str) -> None:
    body = {"type": uri, "title": "Refused", "detail": "refused", "instance": "/api/auth/authorize"}
    assert model.model_validate(body).problem_dump() == body
    assert model.model_json_schema()["properties"]["type"]["const"] == uri
    with pytest.raises(ValidationError):
        model.model_validate({**body, "type": CLAIM_CONFLICT})


@pytest.mark.parametrize(
    ("model", "uri", "error"),
    [
        (LoginFailedProblem, LOGIN_FAILED, "login_failed"),
        (SsoRefusedProblem, SSO_REFUSED, "sso_refused"),
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
        type=RUNNER_RETIRE_INTERRUPTED,
        title="Retirement interrupted",
        detail="retirement interrupted",
        instance="/api/fleet/host/runners/r/retire",
        status=409,
    )
    assert body.problem_dump()["type"] == RUNNER_RETIRE_INTERRUPTED
    assert body.model_json_schema()["properties"]["type"]["const"] == RUNNER_RETIRE_INTERRUPTED


def test_pause_missing_chunk_precheck_and_race_share_semantics() -> None:
    # Both the precheck and ChunkNotFound under the pause lock emit this exact detail.
    body = ChunkNotFoundProblem(
        type=CHUNK_NOT_FOUND,
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
                "type": RUNNER_IDENTITY,
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
        "type": RUNNER_IDENTITY,
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
            "type": RUNNER_IDENTITY,
            "title": "Denied",
            "detail": "denied",
            "instance": "/",
            "reason": "missing",
            "status": status,
            "future": None,
        }
    )
    expected: dict[str, object] = {
        "type": RUNNER_IDENTITY,
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
    declared = {value for module in _wire_modules() for value in _local_problem_constants(module).values()}
    assert declared == set(ALL_PROBLEM_TYPES)


def _local_models(module: ModuleType) -> Iterator[type[BaseModel]]:
    return (
        model
        for name, model in inspect.getmembers(module, inspect.isclass)
        if name.isidentifier()
        and model.__module__ == module.__name__
        and issubclass(model, BaseModel)
        and model.__pydantic_generic_metadata__["origin"] is None
    )


def _problem_literal_values(model: type[BaseModel]) -> set[str]:
    field = model.model_fields.get("type")
    if field is None:
        return set()
    return {
        value
        for value in get_args(field.annotation)
        if isinstance(value, str) and value.startswith("urn:blizzard:problem:")
    }


def test_every_problem_model_literal_is_registered() -> None:
    declared = {
        value
        for module in _wire_modules()
        for model in _local_models(module)
        for value in _problem_literal_values(model)
    }
    assert declared <= ALL_PROBLEM_TYPES


def test_problem_model_census_ignores_generated_generic_specializations(monkeypatch: pytest.MonkeyPatch) -> None:
    uri = "urn:blizzard:problem:external-census-control"
    generated = ProblemDetails[Literal["urn:blizzard:problem:external-census-control"]]
    monkeypatch.setattr(registry, "external_generated_control", generated, raising=False)
    assert uri not in {v for model in _local_models(registry) for v in _problem_literal_values(model)}

    class NamedProblem(generated):
        pass

    monkeypatch.setattr(registry, "external_named_control", NamedProblem, raising=False)
    assert uri not in {v for model in _local_models(registry) for v in _problem_literal_values(model)}
    monkeypatch.setattr(NamedProblem, "__module__", registry.__name__)
    assert uri in {v for model in _local_models(registry) for v in _problem_literal_values(model)}


def test_every_problem_model_literal_has_a_local_final() -> None:
    for module in _wire_modules():
        local_values = set(_local_problem_constants(module).values())
        for model in _local_models(module):
            missing = _problem_literal_values(model) - local_values
            assert not missing, (
                f"{module.__name__}.{model.__name__} has problem Literal(s) without a local Final: {sorted(missing)}"
            )


def test_every_concept_type_is_registered_exactly_once() -> None:
    contributions: list[str] = []
    for module in _wire_modules():
        tree = ast.parse(inspect.getsource(module))
        names = [
            node.target.id
            for node in tree.body
            if isinstance(node, ast.AnnAssign)
            and isinstance(node.target, ast.Name)
            and (node.target.id == "PROBLEM_TYPES" or node.target.id.endswith("_PROBLEM_TYPES"))
            and node.target.id != "ALL_PROBLEM_TYPES"
        ]
        local = set(_local_problem_constants(module).values())
        assert len(names) == (1 if local else 0), (
            f"{module.__name__}: expected one local contribution for its constants"
        )
        for name in names:
            contribution: object = getattr(module, name)
            assert isinstance(contribution, frozenset)
            assert contribution == local, f"{module.__name__}.{name} differs from its local constants"
            contributions.extend(local)
    assert len(contributions) == len(set(contributions)), "problem type contributed more than once"
    assert set(contributions) == ALL_PROBLEM_TYPES


#: The refusals docs/versioning.md lets keep a non-string `detail`; a new problem type sends a string.
_STRUCTURED_DETAIL_TYPES: Final = frozenset(
    {
        TENANT_REQUIRED,
        PROJECT_REQUIRED,
        FLAG_OFF,
        UNKNOWN_PROJECT,
        CONFIG_DOCUMENT_INVALID,
        UNKNOWN_WORK_SOURCE,
        CONFIG_FIELD_INVALID,
        LANDING_ORDER_CONFLICT,
        LAST_TENANT,
        SECRET_IN_USE,
        FORGE_IN_USE,
        FORGE_REVISION_CONFLICT,
        FORGE_ORIGIN_TAKEN,
        FORGE_SLUG_TAKEN,
    }
)


def _is_string_detail(annotation: object) -> bool:
    """`str`, or a `Literal` of strings only (as `tenant-not-found`'s `Literal['no such tenant']` is)."""
    if annotation is str:
        return True
    return get_origin(annotation) is Literal and all(isinstance(arg, str) for arg in get_args(annotation))


def test_only_released_refusals_carry_a_structured_detail() -> None:
    structured = {
        value
        for module in _wire_modules()
        for model in _local_models(module)
        if (values := _problem_literal_values(model)) and not _is_string_detail(model.model_fields["detail"].annotation)
        for value in values
    }
    assert structured <= _STRUCTURED_DETAIL_TYPES

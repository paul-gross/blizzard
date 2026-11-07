"""Wire problem members and stable identifiers for typed runner refusals.

HTTP status is authoritative: legacy claim problems can instead carry a string
``status``, or omit it altogether. These models are not strict RFC 9457 bodies.
"""

from collections.abc import Collection
from typing import Any, Final, Literal, cast

from pydantic import (
    BaseModel,
    ConfigDict,
    GetJsonSchemaHandler,
    SerializerFunctionWrapHandler,
    model_serializer,
    model_validator,
)
from pydantic.json_schema import JsonSchemaValue
from pydantic_core import CoreSchema

CLAIM_CONFLICT: Final = "urn:blizzard:problem:claim-conflict"
CLAIM_TERMINAL: Final = "urn:blizzard:problem:claim-terminal"
CLAIM_DEPENDENCY: Final = "urn:blizzard:problem:claim-dependency"
CLAIM_INCOMPATIBLE: Final = "urn:blizzard:problem:claim-incompatible"
CLAIM_PAUSED: Final = "urn:blizzard:problem:claim-paused"
CLAIM_RUNNER_RETIRED: Final = "urn:blizzard:problem:claim-runner-retired"
CLAIM_RUNNER_UNREGISTERED: Final = "urn:blizzard:problem:claim-runner-unregistered"
CLAIM_RUNNER_VERSION_REFUSED: Final = "urn:blizzard:problem:claim-runner-version-refused"
CLAIM_NOT_READY: Final = "urn:blizzard:problem:claim-not-ready"
RUNNER_IDENTITY: Final = "urn:blizzard:problem:runner-identity"
RUNNER_VERSION: Final = "urn:blizzard:problem:runner-version"
RELEASE_REFUSAL: Final = "urn:blizzard:problem:release-refusal"
TENANT_REQUIRED: Final = "urn:blizzard:problem:tenant-required"
TENANT_NOT_FOUND: Final = "urn:blizzard:problem:tenant-not-found"
TENANT_CREDENTIAL_MISMATCH: Final = "urn:blizzard:problem:tenant-credential-mismatch"
AUTHENTICATION_REQUIRED: Final = "urn:blizzard:problem:authentication-required"
AUTHENTICATION_REVOKED: Final = "urn:blizzard:problem:authentication-revoked"
AUTHENTICATION_UNRESOLVED: Final = "urn:blizzard:problem:authentication-unresolved"
PINNED_GRAPH_MISSING: Final = "urn:blizzard:problem:pinned-graph-missing"
MALFORMED_CURSOR: Final = "urn:blizzard:problem:malformed-cursor"
UNKNOWN_SERVED_PROJECT: Final = "urn:blizzard:problem:unknown-served-project"
UNKNOWN_PROJECT: Final = "urn:blizzard:problem:unknown-project"
CHUNK_NOT_FOUND: Final = "urn:blizzard:problem:chunk-not-found"
CHUNK_NOT_PAUSABLE: Final = "urn:blizzard:problem:chunk-not-pausable"
NO_CURRENT_NODE: Final = "urn:blizzard:problem:no-current-node"
NO_RUN_CONTEXT: Final = "urn:blizzard:problem:no-run-context"
NO_ANSWERED_PROPOSAL: Final = "urn:blizzard:problem:no-answered-proposal"
FINDING_NOT_IN_CHUNK: Final = "urn:blizzard:problem:finding-not-in-chunk"
QUESTION_NOT_FOUND: Final = "urn:blizzard:problem:question-not-found"
RUNNER_NOT_FOUND: Final = "urn:blizzard:problem:runner-not-found"
RUNNER_NOT_REGISTERED: Final = "urn:blizzard:problem:runner-not-registered"
RUNNER_RETIRED: Final = "urn:blizzard:problem:runner-retired"
HOST_RETIRED: Final = "urn:blizzard:problem:host-retired"
RUNNER_OWNERSHIP: Final = "urn:blizzard:problem:runner-ownership"
LEASE_OWNERSHIP: Final = "urn:blizzard:problem:lease-ownership"
ROUTE_NOT_FOUND: Final = "urn:blizzard:problem:route-not-found"
ROUTE_NOT_HELD: Final = "urn:blizzard:problem:route-not-held"
ROUTE_TERMINAL: Final = "urn:blizzard:problem:route-terminal"
SYSTEM_ARTIFACT_NOT_FOUND: Final = "urn:blizzard:problem:system-artifact-not-found"
HOST_NAME_TAKEN: Final = "urn:blizzard:problem:host-name-taken"
RUNNER_ALREADY_HOSTED: Final = "urn:blizzard:problem:runner-already-hosted"
RUNNER_LIFECYCLE_CONFLICT: Final = "urn:blizzard:problem:runner-lifecycle-conflict"
RUNNER_HOLDS_ROUTES: Final = "urn:blizzard:problem:runner-holds-routes"
RUNNER_RETIRE_INTERRUPTED: Final = "urn:blizzard:problem:runner-retire-interrupted"
PERMISSION_REQUIRED: Final = "urn:blizzard:problem:permission-required"
MACHINE_CREDENTIAL_FORBIDDEN: Final = "urn:blizzard:problem:machine-credential-forbidden"
RUNNER_CREDENTIAL_FORBIDDEN: Final = "urn:blizzard:problem:runner-credential-forbidden"
IDP_DISABLED: Final = "urn:blizzard:problem:idp-disabled"
FEDERATION_RESPONSE_MODE: Final = "urn:blizzard:problem:federation-response-mode"
FEDERATION_CLIENT_REFUSED: Final = "urn:blizzard:problem:federation-client-refused"
FEDERATION_NO_PROVIDER: Final = "urn:blizzard:problem:federation-no-provider"
CLI_REDIRECT_REFUSED: Final = "urn:blizzard:problem:cli-redirect-refused"
CLI_PKCE_REQUIRED: Final = "urn:blizzard:problem:cli-pkce-required"
CLI_TOKEN_REFUSED: Final = "urn:blizzard:problem:cli-token-refused"
LOGIN_DISABLED: Final = "urn:blizzard:problem:login-disabled"
LOGIN_PROVIDER_UNKNOWN: Final = "urn:blizzard:problem:login-provider-unknown"
LOGIN_THROTTLED: Final = "urn:blizzard:problem:login-throttled"
LOGIN_FAILED: Final = "urn:blizzard:problem:login-failed"
SSO_REFUSED: Final = "urn:blizzard:problem:sso-refused"
PROJECT_REQUIRED: Final = "urn:blizzard:problem:project-required"
HUB_STORE_UNAVAILABLE: Final = "urn:blizzard:problem:hub-store-unavailable"
FLAG_OFF: Final = "urn:blizzard:problem:flag-off"
INVARIANTS_IN_PROGRESS: Final = "urn:blizzard:problem:invariants-in-progress"
SECRET_ALREADY_EXISTS: Final = "urn:blizzard:problem:secret-already-exists"
SECRET_NOT_FOUND: Final = "urn:blizzard:problem:secret-not-found"
DECISION_ALREADY_RESOLVED: Final = "urn:blizzard:problem:decision-already-resolved"
QUESTION_ALREADY_ANSWERED: Final = "urn:blizzard:problem:question-already-answered"
EGRESS_BACKFILL_FAILED: Final = "urn:blizzard:problem:egress-backfill-failed"
TRACE_REPLAY_FAILED: Final = "urn:blizzard:problem:trace-replay-failed"

PROBLEM_TYPES: frozenset[str] = frozenset(
    {
        CLAIM_CONFLICT,
        CLAIM_TERMINAL,
        CLAIM_DEPENDENCY,
        CLAIM_INCOMPATIBLE,
        CLAIM_PAUSED,
        CLAIM_RUNNER_RETIRED,
        CLAIM_RUNNER_UNREGISTERED,
        CLAIM_RUNNER_VERSION_REFUSED,
        CLAIM_NOT_READY,
        RUNNER_IDENTITY,
        RUNNER_VERSION,
        RELEASE_REFUSAL,
        TENANT_REQUIRED,
        TENANT_NOT_FOUND,
        TENANT_CREDENTIAL_MISMATCH,
        AUTHENTICATION_REQUIRED,
        AUTHENTICATION_REVOKED,
        AUTHENTICATION_UNRESOLVED,
        PINNED_GRAPH_MISSING,
        MALFORMED_CURSOR,
        UNKNOWN_SERVED_PROJECT,
        UNKNOWN_PROJECT,
        CHUNK_NOT_FOUND,
        CHUNK_NOT_PAUSABLE,
        NO_CURRENT_NODE,
        NO_RUN_CONTEXT,
        NO_ANSWERED_PROPOSAL,
        FINDING_NOT_IN_CHUNK,
        QUESTION_NOT_FOUND,
        RUNNER_NOT_FOUND,
        RUNNER_NOT_REGISTERED,
        RUNNER_RETIRED,
        HOST_RETIRED,
        RUNNER_OWNERSHIP,
        LEASE_OWNERSHIP,
        ROUTE_NOT_FOUND,
        ROUTE_NOT_HELD,
        ROUTE_TERMINAL,
        SYSTEM_ARTIFACT_NOT_FOUND,
        HOST_NAME_TAKEN,
        RUNNER_ALREADY_HOSTED,
        RUNNER_LIFECYCLE_CONFLICT,
        RUNNER_HOLDS_ROUTES,
        RUNNER_RETIRE_INTERRUPTED,
        PERMISSION_REQUIRED,
        MACHINE_CREDENTIAL_FORBIDDEN,
        RUNNER_CREDENTIAL_FORBIDDEN,
        IDP_DISABLED,
        FEDERATION_RESPONSE_MODE,
        FEDERATION_CLIENT_REFUSED,
        FEDERATION_NO_PROVIDER,
        CLI_REDIRECT_REFUSED,
        CLI_PKCE_REQUIRED,
        CLI_TOKEN_REFUSED,
        LOGIN_DISABLED,
        LOGIN_PROVIDER_UNKNOWN,
        LOGIN_THROTTLED,
        LOGIN_FAILED,
        SSO_REFUSED,
        PROJECT_REQUIRED,
        HUB_STORE_UNAVAILABLE,
        FLAG_OFF,
        INVARIANTS_IN_PROGRESS,
        SECRET_ALREADY_EXISTS,
        SECRET_NOT_FOUND,
        DECISION_ALREADY_RESOLVED,
        QUESTION_ALREADY_ANSWERED,
        EGRESS_BACKFILL_FAILED,
        TRACE_REPLAY_FAILED,
    }
)

#: The ``401`` types a credential refusal carries.
AUTHENTICATION_TYPES: frozenset[str] = frozenset(
    {AUTHENTICATION_REQUIRED, AUTHENTICATION_REVOKED, AUTHENTICATION_UNRESOLVED}
)


def type_of(payload: object) -> tuple[bool, object]:
    """Whether a decoded refusal body carries a problem ``type``, and that type when it does."""
    body = cast(dict[str, object], payload) if isinstance(payload, dict) else {}
    return ("type" in body), body.get("type")


def admits(payload: object, kinds: Collection[str]) -> bool:
    """Whether a decoded refusal body is one of ``kinds``: a body without a ``type`` (an older hub's)
    is admitted, one with a type outside ``kinds`` is not."""
    present, kind = type_of(payload)
    return not present or (isinstance(kind, str) and kind in kinds)


class ValidationEntry(BaseModel):
    """One entry of a FastAPI request-validation ``detail`` list."""

    loc: list[str | int] = []
    msg: str = ""
    type: str = ""


class Problem(BaseModel):
    """The members every hub refusal shares, as a client that dispatches on ``type`` reads them. ``detail`` is a
    string, or — while the legacy fields stay — an object (``message`` and a ``code``) or a validation list. Every
    member is optional because a refusal from a hub before problem details carries only ``detail``."""

    type: str | None = None
    title: str | None = None
    detail: str | dict[str, Any] | list[ValidationEntry] | None = None
    instance: str | None = None
    status: int | str | None = None


class CommonProblemMembers[ProblemType: str, Detail](BaseModel):
    """Always-sent common members, without imposing a status representation."""

    model_config = ConfigDict(extra="allow", json_schema_serialization_defaults_required=False)

    type: ProblemType
    title: str
    detail: Detail
    instance: str

    @classmethod
    def __get_pydantic_json_schema__(cls, core_schema: CoreSchema, handler: GetJsonSchemaHandler) -> JsonSchemaValue:
        schema = handler(core_schema)
        if handler.mode == "serialization":
            # problem_dump sends defaults and nullable extensions, but omits an
            # absent numeric status. A legacy string status remains required.
            properties = schema.get("properties", {})
            required = set(schema.get("required", []))
            required.update(name for name in properties if name != "status")
            schema["required"] = [name for name in properties if name in required]
            status = properties.get("status")
            if status and "status" not in required and "anyOf" in status:
                status["anyOf"] = [variant for variant in status["anyOf"] if variant.get("type") != "null"]
                status.pop("default", None)
        return schema

    @model_serializer(mode="wrap")
    def serialize_without_null_status(self, handler: SerializerFunctionWrapHandler):
        body = cast(dict[str, Any], handler(self))
        if body.get("status", ...) is None:
            del body["status"]
        return body

    def problem_dump(self) -> dict[str, Any]:
        """Omit only absent numeric status, never nullable legacy extension fields."""
        return self.model_dump(mode="json", by_alias=True)


class ProblemMembers[ProblemType: str](CommonProblemMembers[ProblemType, str]):
    """Common members for a string-valued detail."""


class StatuslessProblemMembers[ProblemType: str](ProblemMembers[ProblemType]):
    """Legacy claim refusals must not carry status, including as an extension."""

    @model_validator(mode="before")
    @classmethod
    def reject_status(cls, value: Any) -> Any:
        if isinstance(value, dict) and "status" in value:
            raise ValueError("status must be absent")
        return cast(Any, value)

    @classmethod
    def __get_pydantic_json_schema__(cls, core_schema: CoreSchema, handler: GetJsonSchemaHandler) -> JsonSchemaValue:
        schema = super().__get_pydantic_json_schema__(core_schema, handler)
        schema["not"] = {"required": ["status"]}
        return schema

    @model_serializer(mode="wrap")
    def serialize_without_status(self, handler: SerializerFunctionWrapHandler):
        body = handler(self)
        body.pop("status", None)
        return cast(dict[str, Any], body)


class ProblemDetails[ProblemType: str](ProblemMembers[ProblemType]):
    """A normal problem with an optional numeric HTTP status.

    ``problem_dump`` omits an absent status without suppressing nullable extension fields.
    """

    status: int | None = None


class HubStoreUnavailableProblem(ProblemDetails[Literal["urn:blizzard:problem:hub-store-unavailable"]]):
    """The hub has no wired store available to service the request."""

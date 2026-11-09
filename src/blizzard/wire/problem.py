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

HUB_STORE_UNAVAILABLE: Final = "urn:blizzard:problem:hub-store-unavailable"


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


PROBLEM_TYPES: frozenset[str] = frozenset({HUB_STORE_UNAVAILABLE})

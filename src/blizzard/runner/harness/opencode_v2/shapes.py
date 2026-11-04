"""The OpenCode 2 shapes the binding reads: sessions, messages and their parts, events, permission and form requests,
inbox items, error records, and the model listing. Each parser keeps only what the binding reads and refuses a shape
missing it; a message or part of a type it does not read is kept by its type alone."""

from __future__ import annotations

from dataclasses import dataclass, field

from blizzard.foundation.roles import domain_model
from blizzard.runner.harness.json_value import JsonObject, JsonValue
from blizzard.runner.harness.opencode_v2.client import ModelReference


class OpenCodeTwoShapeError(ValueError):
    """A service record lacks a field the binding reads, or carries it with the wrong JSON type."""


def _object(value: JsonValue, what: str) -> JsonObject:
    if not isinstance(value, dict):
        raise OpenCodeTwoShapeError(f"{what} is not an object")
    return value


def _optional_object(record: JsonObject, key: str, what: str) -> JsonObject:
    value = record.get(key)
    return {} if value is None else _object(value, f"{what}.{key}")


def _text(record: JsonObject, key: str, what: str) -> str:
    value = record.get(key)
    if not isinstance(value, str):
        raise OpenCodeTwoShapeError(f"{what} lacks a string {key!r}")
    return value


def _optional_text(record: JsonObject, key: str, what: str) -> str | None:
    value = record.get(key)
    if value is not None and not isinstance(value, str):
        raise OpenCodeTwoShapeError(f"{what}.{key} is not a string")
    return value


def _optional_int(record: JsonObject, key: str, what: str) -> int | None:
    value = record.get(key)
    if value is None:
        return None
    if not isinstance(value, int) or isinstance(value, bool):
        raise OpenCodeTwoShapeError(f"{what}.{key} is not an integer")
    return value


def _int(record: JsonObject, key: str, what: str) -> int:
    value = _optional_int(record, key, what)
    if value is None:
        raise OpenCodeTwoShapeError(f"{what} lacks an integer {key!r}")
    return value


def _number(record: JsonObject, key: str, what: str) -> float:
    value = record.get(key)
    if not isinstance(value, int | float) or isinstance(value, bool):
        raise OpenCodeTwoShapeError(f"{what} lacks a number {key!r}")
    return float(value)


def _list(record: JsonObject, key: str, what: str) -> list[JsonValue]:
    value = record.get(key)
    if value is None:
        return []
    if not isinstance(value, list):
        raise OpenCodeTwoShapeError(f"{what}.{key} is not a list")
    return value


def _texts(record: JsonObject, key: str, what: str) -> tuple[str, ...]:
    items = _list(record, key, what)
    if not all(isinstance(item, str) for item in items):
        raise OpenCodeTwoShapeError(f"{what}.{key} is not a list of strings")
    return tuple(str(item) for item in items)


@domain_model
@dataclass(frozen=True)
class Tokens:
    input: int = 0
    output: int = 0
    reasoning: int = 0
    cache_read: int = 0
    cache_write: int = 0

    @property
    def context(self) -> int:
        """The context one step filled: what it read, cached or not, and what it wrote."""
        return self.input + self.cache_read + self.cache_write + self.output


def parse_tokens(value: JsonValue, what: str = "tokens") -> Tokens:
    record = _object(value, what)
    cache = _optional_object(record, "cache", what)
    return Tokens(
        input=_int(record, "input", what),
        output=_int(record, "output", what),
        reasoning=_int(record, "reasoning", what),
        cache_read=_optional_int(cache, "read", what) or 0,
        cache_write=_optional_int(cache, "write", what) or 0,
    )


def parse_model_reference(value: JsonValue, what: str = "model") -> ModelReference:
    record = _object(value, what)
    return ModelReference(
        provider_id=_text(record, "providerID", what),
        id=_text(record, "id", what),
        variant=_optional_text(record, "variant", what),
    )


@domain_model
@dataclass(frozen=True)
class StructuredError:
    """A session error record: its ``type`` names the class, ``retry_after_ms`` the provider's reset when sent."""

    type: str
    message: str = ""
    status: int | None = None
    retry_after_ms: int | None = None


def parse_error(value: JsonValue, what: str = "error") -> StructuredError:
    record = _object(value, what)
    return StructuredError(
        type=_text(record, "type", what),
        message=_optional_text(record, "message", what) or "",
        status=_optional_int(record, "status", what),
        retry_after_ms=_optional_int(record, "retryAfterMs", what),
    )


@domain_model
@dataclass(frozen=True)
class SessionRecord:
    id: str
    directory: str
    metadata: JsonObject = field(default_factory=dict[str, JsonValue])
    parent_id: str | None = None
    model: ModelReference | None = None
    agent: str | None = None
    outcome: str | None = None
    tokens: Tokens = Tokens()
    cost: float = 0.0


def parse_session(value: JsonValue) -> SessionRecord:
    record = _object(value, "session")
    location = _object(record.get("location"), "session.location")
    model = record.get("model")
    return SessionRecord(
        id=_text(record, "id", "session"),
        directory=_text(location, "directory", "session.location"),
        metadata=_optional_object(record, "metadata", "session"),
        parent_id=_optional_text(record, "parentID", "session"),
        model=None if model is None else parse_model_reference(model, "session.model"),
        agent=_optional_text(record, "agent", "session"),
        outcome=_optional_text(record, "outcome", "session"),
        tokens=parse_tokens(record.get("tokens"), "session.tokens"),
        cost=_number(record, "cost", "session"),
    )


@domain_model
@dataclass(frozen=True)
class ToolState:
    status: str
    input: JsonObject = field(default_factory=dict[str, JsonValue])
    metadata: JsonObject = field(default_factory=dict[str, JsonValue])


@domain_model
@dataclass(frozen=True)
class Part:
    """One part of an assistant message. ``text`` is a text or reasoning part's; ``state`` a tool part's."""

    type: str
    text: str = ""
    id: str | None = None
    name: str | None = None
    state: ToolState | None = None


def parse_part(value: JsonValue) -> Part:
    record = _object(value, "part")
    kind = _text(record, "type", "part")
    if kind in ("text", "reasoning"):
        return Part(type=kind, text=_text(record, "text", f"part.{kind}"))
    if kind != "tool":
        return Part(type=kind)
    state = _object(record.get("state"), "part.tool.state")
    return Part(
        type=kind,
        id=_text(record, "id", "part.tool"),
        name=_text(record, "name", "part.tool"),
        state=ToolState(
            status=_text(state, "status", "part.tool.state"),
            input=_optional_object(state, "input", "part.tool.state"),
            metadata=_optional_object(state, "metadata", "part.tool.state"),
        ),
    )


@domain_model
@dataclass(frozen=True)
class Message:
    """One session message. ``parts`` and ``finish`` are an assistant step's; ``tokens``, ``cost`` and ``model`` an
    assistant step's or a compaction's; ``outcome`` an idle's; ``error`` an assistant step's or an idle's; ``text`` a
    user or synthetic message's."""

    type: str
    id: str
    created: int
    text: str = ""
    parts: tuple[Part, ...] = ()
    tokens: Tokens | None = None
    cost: float | None = None
    finish: str | None = None
    model: ModelReference | None = None
    outcome: str | None = None
    error: StructuredError | None = None

    def final_text(self) -> str:
        return "".join(part.text for part in self.parts if part.type == "text")


def parse_message(value: JsonValue) -> Message:
    record = _object(value, "message")
    kind = _text(record, "type", "message")
    what = f"message.{kind}"
    created = _int(_object(record.get("time"), f"{what}.time"), "created", f"{what}.time")
    base = Message(type=kind, id=_text(record, "id", what), created=created)
    error = record.get("error")
    parsed_error = None if error is None else parse_error(error, f"{what}.error")
    if kind == "compaction":
        model = record.get("model")
        return Message(
            type=kind,
            id=base.id,
            created=created,
            tokens=parse_tokens(record.get("tokens"), f"{what}.tokens"),
            cost=_number(record, "cost", what),
            model=None if model is None else parse_model_reference(model, f"{what}.model"),
        )
    if kind == "assistant":
        model = record.get("model")
        return Message(
            type=kind,
            id=base.id,
            created=created,
            parts=tuple(parse_part(part) for part in _list(record, "content", what)),
            tokens=parse_tokens(record.get("tokens"), f"{what}.tokens"),
            cost=_number(record, "cost", what),
            finish=_optional_text(record, "finish", what),
            model=None if model is None else parse_model_reference(model, f"{what}.model"),
            error=parsed_error,
        )
    if kind == "idle":
        return Message(
            type=kind, id=base.id, created=created, outcome=_text(record, "outcome", what), error=parsed_error
        )
    if kind in ("user", "synthetic"):
        return Message(type=kind, id=base.id, created=created, text=_text(record, "text", what))
    return base


@domain_model
@dataclass(frozen=True)
class PermissionRequest:
    id: str
    session_id: str
    action: str
    resources: tuple[str, ...] = ()


def parse_permission_request(value: JsonValue) -> PermissionRequest:
    record = _object(value, "permission request")
    return PermissionRequest(
        id=_text(record, "id", "permission request"),
        session_id=_text(record, "sessionID", "permission request"),
        action=_text(record, "action", "permission request"),
        resources=_texts(record, "resources", "permission request"),
    )


@domain_model
@dataclass(frozen=True)
class FormRequest:
    id: str
    session_id: str
    title: str = ""


def parse_form_request(value: JsonValue) -> FormRequest:
    record = _object(value, "form request")
    return FormRequest(
        id=_text(record, "id", "form request"),
        session_id=_text(record, "sessionID", "form request"),
        title=_optional_text(record, "title", "form request") or "",
    )


@domain_model
@dataclass(frozen=True)
class InboxItem:
    id: str


def parse_inbox_item(value: JsonValue) -> InboxItem:
    return InboxItem(id=_text(_object(value, "inbox item"), "id", "inbox item"))


@domain_model
@dataclass(frozen=True)
class Event:
    """One event-stream event; ``session_id`` is the session it concerns, when it names one."""

    id: str
    type: str
    session_id: str | None = None


def parse_event(value: JsonValue) -> Event:
    record = _object(value, "event")
    data = _optional_object(record, "data", "event")
    durable = _optional_object(record, "durable", "event")
    session_id = _optional_text(data, "sessionID", "event.data") or _optional_text(
        durable, "aggregateID", "event.durable"
    )
    return Event(id=_text(record, "id", "event"), type=_text(record, "type", "event"), session_id=session_id)


@domain_model
@dataclass(frozen=True)
class ListedModel:
    provider_id: str
    id: str
    variants: tuple[str, ...] = ()


def parse_listed_model(value: JsonValue) -> ListedModel:
    record = _object(value, "model")
    variants = _list(record, "variants", "model")
    return ListedModel(
        provider_id=_text(record, "providerID", "model"),
        id=_text(record, "id", "model"),
        variants=tuple(_text(_object(variant, "model.variant"), "id", "model.variant") for variant in variants),
    )


__all__ = [
    "Event",
    "FormRequest",
    "InboxItem",
    "ListedModel",
    "Message",
    "OpenCodeTwoShapeError",
    "Part",
    "PermissionRequest",
    "SessionRecord",
    "StructuredError",
    "Tokens",
    "ToolState",
    "parse_error",
    "parse_event",
    "parse_form_request",
    "parse_inbox_item",
    "parse_listed_model",
    "parse_message",
    "parse_model_reference",
    "parse_part",
    "parse_permission_request",
    "parse_session",
    "parse_tokens",
]

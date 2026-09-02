"""Strict, dependency-free parsers for the observed OpenCode 1.18.25 shapes.

Only required discriminators and fields are accepted. Unknown kinds raise
:class:`UnknownOpenCodeShapeError`; malformed fields raise :class:`OpenCodeShapeError`. Additional
fields stay in ``raw``, while a new required discriminator cannot pass silently.
"""

from __future__ import annotations

import json
import math
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Literal, cast

OpenCodeEventType = Literal[
    "step_start", "text", "reasoning", "tool_use", "step_finish", "error", "permission", "compaction"
]
OpenCodePartType = Literal[
    "step-start",
    "text",
    "reasoning",
    "tool",
    "step-finish",
    "compaction",
    "snapshot",
    "patch",
    "agent",
    "subtask",
]
OpenCodeToolStatus = Literal["pending", "running", "completed", "error"]

KNOWN_EVENT_TYPES = frozenset(
    {"step_start", "text", "reasoning", "tool_use", "step_finish", "error", "permission", "compaction"}
)
KNOWN_PART_TYPES = frozenset(
    {"step-start", "text", "reasoning", "tool", "step-finish", "compaction", "snapshot", "patch", "agent", "subtask"}
)
KNOWN_TOOL_STATUSES = frozenset({"pending", "running", "completed", "error"})


class OpenCodeShapeError(ValueError):
    """A required OpenCode field has the wrong shape or is missing."""


class UnknownOpenCodeShapeError(OpenCodeShapeError):
    """OpenCode emitted a discriminator this pinned parser does not know."""


def _object(value: object, where: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise OpenCodeShapeError(f"{where} must be an object, got {type(value).__name__}")
    return value


def _required(value: Mapping[str, Any], key: str, where: str) -> object:
    if key not in value:
        raise OpenCodeShapeError(f"{where} is missing required field {key!r}")
    return value[key]


def _string(value: Mapping[str, Any], key: str, where: str) -> str:
    raw = _required(value, key, where)
    if not isinstance(raw, str) or not raw:
        raise OpenCodeShapeError(f"{where}.{key} must be a non-empty string")
    return raw


def _optional_string(value: Mapping[str, Any], key: str, where: str, *, allow_empty: bool = False) -> str | None:
    if key not in value or value[key] is None:
        return None
    raw = value[key]
    if not isinstance(raw, str) or (not raw and not allow_empty):
        raise OpenCodeShapeError(f"{where}.{key} must be a non-empty string or null")
    return raw


def _integer(value: Mapping[str, Any], key: str, where: str) -> int:
    raw = _required(value, key, where)
    if not isinstance(raw, int) or isinstance(raw, bool) or raw < 0:
        raise OpenCodeShapeError(f"{where}.{key} must be a non-negative integer")
    return raw


def _number(value: Mapping[str, Any], key: str, where: str) -> float:
    raw = _required(value, key, where)
    if not isinstance(raw, int | float) or isinstance(raw, bool) or raw < 0 or not math.isfinite(float(raw)):
        raise OpenCodeShapeError(f"{where}.{key} must be a non-negative number")
    return float(raw)


def _optional_number(value: Mapping[str, Any], key: str, where: str) -> float | None:
    if key not in value or value[key] is None:
        return None
    raw = value[key]
    if not isinstance(raw, int | float) or isinstance(raw, bool) or raw < 0 or not math.isfinite(float(raw)):
        raise OpenCodeShapeError(f"{where}.{key} must be a non-negative number or null")
    return float(raw)


def _list(value: Mapping[str, Any], key: str, where: str) -> list[object]:
    raw = _required(value, key, where)
    if not isinstance(raw, list):
        raise OpenCodeShapeError(f"{where}.{key} must be an array")
    return raw


@dataclass(frozen=True)
class OpenCodeTokenUsage:
    """The token breakdown carried by an OpenCode ``step-finish`` shape."""

    input_tokens: int
    output_tokens: int
    reasoning_tokens: int
    cache_read_tokens: int
    cache_write_tokens: int

    @classmethod
    def parse(cls, value: object, *, where: str = "tokens") -> OpenCodeTokenUsage:
        tokens = _object(value, where)
        cache = _object(_required(tokens, "cache", where), f"{where}.cache")
        return cls(
            input_tokens=_integer(tokens, "input", where),
            output_tokens=_integer(tokens, "output", where),
            reasoning_tokens=_integer(tokens, "reasoning", where),
            cache_read_tokens=_integer(cache, "read", f"{where}.cache"),
            cache_write_tokens=_integer(cache, "write", f"{where}.cache"),
        )


@dataclass(frozen=True)
class OpenCodeToolState:
    """A pending, running, completed, or failed tool invocation state."""

    status: OpenCodeToolStatus
    input: Mapping[str, Any]
    output: str | None
    error: str | None
    title: str | None

    @classmethod
    def parse(cls, value: object, *, where: str = "tool.state") -> OpenCodeToolState:
        state = _object(value, where)
        raw_status = _string(state, "status", where)
        if raw_status not in KNOWN_TOOL_STATUSES:
            raise UnknownOpenCodeShapeError(f"{where}.status has unknown value {raw_status!r}")
        status = cast(OpenCodeToolStatus, raw_status)
        raw_input = _object(_required(state, "input", where), f"{where}.input")
        output = _optional_string(state, "output", where, allow_empty=True)
        error = _optional_string(state, "error", where)
        title = _optional_string(state, "title", where)
        if status == "completed" and output is None:
            raise OpenCodeShapeError(f"{where} with status 'completed' needs output")
        if status == "error" and error is None:
            raise OpenCodeShapeError(f"{where} with status 'error' needs error")
        return cls(status, raw_input, output, error, title)


@dataclass(frozen=True)
class OpenCodePart:
    """The stable identity and contract fields of one exported message part."""

    id: str
    session_id: str
    message_id: str
    type: OpenCodePartType
    text: str | None
    call_id: str | None
    tool: str | None
    state: OpenCodeToolState | None
    reason: str | None
    tokens: OpenCodeTokenUsage | None
    cost: float | None
    tail_start_id: str | None
    raw: Mapping[str, Any]

    @classmethod
    def parse(cls, value: object, *, where: str = "part") -> OpenCodePart:
        part = _object(value, where)
        part_id = _string(part, "id", where)
        session_id = _string(part, "sessionID", where)
        message_id = _string(part, "messageID", where)
        raw_type = _string(part, "type", where)
        if raw_type not in KNOWN_PART_TYPES:
            raise UnknownOpenCodeShapeError(f"{where}.type has unknown value {raw_type!r}")
        part_type = cast(OpenCodePartType, raw_type)

        text = _optional_string(part, "text", where, allow_empty=True)
        call_id = _optional_string(part, "callID", where)
        tool = _optional_string(part, "tool", where)
        state = None
        reason = None
        tokens = None
        cost = None
        # A compaction part's model-replay head; the spelling is unconfirmed against a live
        # compaction, declared at `blizzard-context:/verification/blizzard/gaps.md`.
        tail_start_id = _optional_string(part, "tail_start_id", where)
        if part_type == "text" or part_type == "reasoning":
            if "text" not in part or not isinstance(part["text"], str):
                raise OpenCodeShapeError(f"{where}.text must be a string for {part_type!r} parts")
        elif part_type == "tool":
            if call_id is None:
                raise OpenCodeShapeError(f"{where}.callID is required for tool parts")
            if tool is None:
                raise OpenCodeShapeError(f"{where}.tool is required for tool parts")
            state = OpenCodeToolState.parse(_required(part, "state", where), where=f"{where}.state")
        elif part_type == "step-finish":
            reason = _string(part, "reason", where)
            tokens = OpenCodeTokenUsage.parse(_required(part, "tokens", where), where=f"{where}.tokens")
            cost = _number(part, "cost", where)
        return cls(
            id=part_id,
            session_id=session_id,
            message_id=message_id,
            type=part_type,
            text=text,
            call_id=call_id,
            tool=tool,
            state=state,
            reason=reason,
            tokens=tokens,
            cost=cost,
            tail_start_id=tail_start_id,
            raw=part,
        )


@dataclass(frozen=True)
class OpenCodeError:
    """The provider/process error envelope used by a JSON run event."""

    name: str
    message: str
    status_code: int | None
    raw: Mapping[str, Any]

    @classmethod
    def parse(cls, value: object, *, where: str = "error") -> OpenCodeError:
        error = _object(value, where)
        name = _string(error, "name", where)
        data = _object(_required(error, "data", where), f"{where}.data")
        message = _string(data, "message", f"{where}.data")
        status_code = None
        if "statusCode" in data:
            raw_status = data["statusCode"]
            if not isinstance(raw_status, int) or isinstance(raw_status, bool) or raw_status < 0:
                raise OpenCodeShapeError(f"{where}.data.statusCode must be a non-negative integer")
            status_code = raw_status
        return cls(name=name, message=message, status_code=status_code, raw=error)


@dataclass(frozen=True)
class OpenCodePermissionRequest:
    """The permission event shape retained for the proof's deny-path evidence."""

    id: str
    permission: str
    patterns: tuple[str, ...]
    raw: Mapping[str, Any]

    @classmethod
    def parse(cls, value: object, *, where: str = "permission") -> OpenCodePermissionRequest:
        permission = _object(value, where)
        permission_id = _string(permission, "id", where)
        name = _string(permission, "permission", where)
        patterns_raw = _list(permission, "patterns", where)
        patterns: list[str] = []
        for index, pattern in enumerate(patterns_raw):
            if not isinstance(pattern, str) or not pattern:
                raise OpenCodeShapeError(f"{where}.patterns[{index}] must be a non-empty string")
            patterns.append(pattern)
        return cls(permission_id, name, tuple(patterns), permission)


_EVENT_PART_TYPES: dict[str, str] = {
    "step_start": "step-start",
    "text": "text",
    "reasoning": "reasoning",
    "tool_use": "tool",
    "step_finish": "step-finish",
    "compaction": "compaction",
}


@dataclass(frozen=True)
class OpenCodeRunEvent:
    """One line emitted by ``opencode run --format json``."""

    type: OpenCodeEventType
    session_id: str
    part: OpenCodePart | None
    error: OpenCodeError | None
    permission: OpenCodePermissionRequest | None
    raw: Mapping[str, Any]

    @classmethod
    def parse(cls, value: object, *, where: str = "event") -> OpenCodeRunEvent:
        event = _object(value, where)
        raw_type = _string(event, "type", where)
        if raw_type not in KNOWN_EVENT_TYPES:
            raise UnknownOpenCodeShapeError(f"{where}.type has unknown value {raw_type!r}")
        event_type = cast(OpenCodeEventType, raw_type)
        session_id = _string(event, "sessionID", where)
        part = None
        error = None
        permission = None
        if event_type == "error":
            error = OpenCodeError.parse(_required(event, "error", where), where=f"{where}.error")
        elif event_type == "permission":
            permission = OpenCodePermissionRequest.parse(
                _required(event, "permission", where), where=f"{where}.permission"
            )
        else:
            part = OpenCodePart.parse(_required(event, "part", where), where=f"{where}.part")
            if part.session_id != session_id:
                raise OpenCodeShapeError(f"{where}.part.sessionID does not match {where}.sessionID")
            expected_type = _EVENT_PART_TYPES[event_type]
            if part.type != expected_type:
                raise OpenCodeShapeError(
                    f"{where}.part.type {part.type!r} does not match {event_type!r}; expected {expected_type!r}"
                )
        return cls(event_type, session_id, part, error, permission, event)


def parse_run_event(value: object) -> OpenCodeRunEvent:
    """Parse one JSON event, rejecting unknown required event kinds explicitly."""

    return OpenCodeRunEvent.parse(value)


def parse_run_events(value: object) -> tuple[OpenCodeRunEvent, ...]:
    """Parse an array of JSON event objects in emitted order."""

    if not isinstance(value, list):
        raise OpenCodeShapeError(f"events must be an array, got {type(value).__name__}")
    return tuple(OpenCodeRunEvent.parse(item, where=f"events[{index}]") for index, item in enumerate(value))


def parse_run_jsonl(value: str) -> tuple[OpenCodeRunEvent, ...]:
    """Parse newline-delimited JSON without swallowing malformed lines."""

    events: list[OpenCodeRunEvent] = []
    for index, line in enumerate(value.splitlines(), start=1):
        if not line.strip():
            continue
        try:
            decoded = json.loads(line)
        except json.JSONDecodeError as exc:
            raise OpenCodeShapeError(f"event line {index} is not JSON: {exc.msg}") from exc
        events.append(OpenCodeRunEvent.parse(decoded, where=f"event line {index}"))
    return tuple(events)


@dataclass(frozen=True)
class OpenCodeSessionInfo:
    """The session metadata in an export or child-session response."""

    id: str
    parent_id: str | None
    directory: str | None
    title: str | None
    project_id: str | None
    raw: Mapping[str, Any]

    @classmethod
    def parse(cls, value: object, *, where: str = "info") -> OpenCodeSessionInfo:
        info = _object(value, where)
        return cls(
            id=_string(info, "id", where),
            parent_id=_optional_string(info, "parentID", where),
            directory=_optional_string(info, "directory", where),
            title=_optional_string(info, "title", where),
            project_id=_optional_string(info, "projectID", where),
            raw=info,
        )


@dataclass(frozen=True)
class OpenCodeMessageInfo:
    """The identity, role, model, and optional step usage in one exported message."""

    id: str
    session_id: str
    role: Literal["user", "assistant"]
    parent_id: str | None
    model_id: str | None
    provider_id: str | None
    variant: str | None
    tokens: OpenCodeTokenUsage | None
    cost: float | None
    raw: Mapping[str, Any]

    @classmethod
    def parse(cls, value: object, *, where: str = "message.info") -> OpenCodeMessageInfo:
        info = _object(value, where)
        message_id = _string(info, "id", where)
        session_id = _string(info, "sessionID", where)
        raw_role = _string(info, "role", where)
        if raw_role not in {"user", "assistant"}:
            raise UnknownOpenCodeShapeError(f"{where}.role has unknown value {raw_role!r}")
        role = cast(Literal["user", "assistant"], raw_role)
        tokens = None
        if "tokens" in info and info["tokens"] is not None:
            tokens = OpenCodeTokenUsage.parse(info["tokens"], where=f"{where}.tokens")
        return cls(
            id=message_id,
            session_id=session_id,
            role=role,
            parent_id=_optional_string(info, "parentID", where),
            model_id=_optional_string(info, "modelID", where),
            provider_id=_optional_string(info, "providerID", where),
            variant=_optional_string(info, "variant", where),
            tokens=tokens,
            cost=_optional_number(info, "cost", where),
            raw=info,
        )


@dataclass(frozen=True)
class OpenCodeMessage:
    """An exported message with its ordered stable parts."""

    info: OpenCodeMessageInfo
    parts: tuple[OpenCodePart, ...]
    raw: Mapping[str, Any]

    @classmethod
    def parse(cls, value: object, *, where: str = "message") -> OpenCodeMessage:
        message = _object(value, where)
        info = OpenCodeMessageInfo.parse(_required(message, "info", where), where=f"{where}.info")
        parts_raw = _list(message, "parts", where)
        parts = tuple(OpenCodePart.parse(item, where=f"{where}.parts[{index}]") for index, item in enumerate(parts_raw))
        part_ids = [part.id for part in parts]
        if len(set(part_ids)) != len(part_ids):
            raise OpenCodeShapeError(f"{where}.parts contains duplicate part identities")
        for index, part in enumerate(parts):
            if part.session_id != info.session_id:
                raise OpenCodeShapeError(f"{where}.parts[{index}] sessionID does not match message.info.sessionID")
            if part.message_id != info.id:
                raise OpenCodeShapeError(f"{where}.parts[{index}] messageID does not match message.info.id")
        return cls(info=info, parts=parts, raw=message)


@dataclass(frozen=True)
class OpenCodeSessionExport:
    """The documented ``{info, messages[{info, parts}]}`` export shape."""

    info: OpenCodeSessionInfo
    messages: tuple[OpenCodeMessage, ...]
    raw: Mapping[str, Any]

    @classmethod
    def parse(cls, value: object, *, where: str = "export") -> OpenCodeSessionExport:
        export = _object(value, where)
        info = OpenCodeSessionInfo.parse(_required(export, "info", where), where=f"{where}.info")
        messages_raw = _list(export, "messages", where)
        messages = tuple(
            OpenCodeMessage.parse(item, where=f"{where}.messages[{index}]") for index, item in enumerate(messages_raw)
        )
        message_ids = [message.info.id for message in messages]
        if len(set(message_ids)) != len(message_ids):
            raise OpenCodeShapeError(f"{where}.messages contains duplicate message identities")
        part_ids = [part.id for message in messages for part in message.parts]
        if len(set(part_ids)) != len(part_ids):
            raise OpenCodeShapeError(f"{where}.messages contains duplicate part identities")
        for index, message in enumerate(messages):
            if message.info.session_id != info.id:
                raise OpenCodeShapeError(f"{where}.messages[{index}] sessionID does not match export.info.id")
        return cls(info=info, messages=messages, raw=export)


def parse_session_export(value: object) -> OpenCodeSessionExport:
    """Parse an OpenCode export and all of its message parts."""

    return OpenCodeSessionExport.parse(value)


@dataclass(frozen=True)
class OpenCodeChildSession:
    """A child returned by ``GET /session/:id/children``."""

    id: str
    parent_id: str
    directory: str | None
    title: str | None
    project_id: str | None
    raw: Mapping[str, Any]

    @classmethod
    def parse(cls, value: object, *, where: str = "child") -> OpenCodeChildSession:
        info = OpenCodeSessionInfo.parse(value, where=where)
        if info.parent_id is None:
            raise OpenCodeShapeError(f"{where}.parentID is required for a child session")
        return cls(info.id, info.parent_id, info.directory, info.title, info.project_id, info.raw)


def parse_child_sessions(value: object) -> tuple[OpenCodeChildSession, ...]:
    """Parse the documented child-session array (or its named response wrapper)."""

    raw_children: object = _required(value, "children", "children response") if isinstance(value, dict) else value
    if not isinstance(raw_children, list):
        raise OpenCodeShapeError(f"children must be an array, got {type(raw_children).__name__}")
    return tuple(
        OpenCodeChildSession.parse(item, where=f"children[{index}]") for index, item in enumerate(raw_children)
    )


@dataclass(frozen=True)
class OpenCodeModelReference:
    """A provider/model selection used by the model-and-variant contract."""

    provider: str
    model: str
    variant: str | None

    @classmethod
    def parse(cls, value: object, *, where: str = "model") -> OpenCodeModelReference:
        if isinstance(value, str):
            raw = value
            variant = None
        else:
            mapping = _object(value, where)
            provider = _string(mapping, "provider", where)
            model = _string(mapping, "model", where)
            variant = _optional_string(mapping, "variant", where)
            _validate_model_component(provider, where)
            _validate_model_component(model, where)
            return cls(provider, model, variant)
        if raw.strip() != raw or raw.count("/") != 1:
            raise OpenCodeShapeError(f"{where} must use the exact provider/model form")
        provider, model = raw.split("/", 1)
        _validate_model_component(provider, where)
        _validate_model_component(model, where)
        return cls(provider, model, variant)


def _validate_model_component(value: str, where: str) -> None:
    if not value or any(character.isspace() or ord(character) < 32 or ord(character) == 127 for character in value):
        raise OpenCodeShapeError(f"{where} must use non-empty provider/model components")
    if "/" in value:
        raise OpenCodeShapeError(f"{where} must use the exact provider/model form")


def parse_model_reference(value: object) -> OpenCodeModelReference:
    """Parse either ``provider/model`` or the explicit provider/model/variant object."""

    return OpenCodeModelReference.parse(value)


@dataclass(frozen=True)
class OpenCodeWorkerConfig:
    """Runner-owned permission and plugin configuration used for isolation evidence."""

    permissions: Mapping[str, str | Mapping[str, str]]
    plugins: tuple[str, ...]
    raw: Mapping[str, Any]

    @classmethod
    def parse(cls, value: object, *, where: str = "config") -> OpenCodeWorkerConfig:
        config = _object(value, where)
        permission_raw = _object(_required(config, "permission", where), f"{where}.permission")
        permissions: dict[str, str | Mapping[str, str]] = {}
        for name, action in permission_raw.items():
            if not isinstance(name, str) or not name:
                raise OpenCodeShapeError(f"{where}.permission has a non-string rule name")
            if isinstance(action, str):
                if action not in {"allow", "ask", "deny"}:
                    raise UnknownOpenCodeShapeError(
                        f"{where}.permission.{name} has unknown action {action!r}; expected allow, ask, or deny"
                    )
                permissions[name] = action
                continue
            if not isinstance(action, dict):
                raise OpenCodeShapeError(f"{where}.permission.{name} must be an action or a pattern/action object")
            patterns: dict[str, str] = {}
            for pattern, pattern_action in action.items():
                if not isinstance(pattern, str) or not pattern:
                    raise OpenCodeShapeError(f"{where}.permission.{name} has a non-string pattern")
                if not isinstance(pattern_action, str) or pattern_action not in {"allow", "ask", "deny"}:
                    raise UnknownOpenCodeShapeError(
                        f"{where}.permission.{name}.{pattern} has unknown action {pattern_action!r}; "
                        "expected allow, ask, or deny"
                    )
                patterns[pattern] = pattern_action
            permissions[name] = patterns
        plugins_raw = _list(config, "plugin", where)
        plugins: list[str] = []
        for index, plugin in enumerate(plugins_raw):
            if not isinstance(plugin, str) or not plugin:
                raise OpenCodeShapeError(f"{where}.plugin[{index}] must be a non-empty string")
            plugins.append(plugin)
        return cls(permissions=permissions, plugins=tuple(plugins), raw=config)


def parse_worker_config(value: object) -> OpenCodeWorkerConfig:
    """Parse the runner-owned permission/plugin configuration shape."""

    return OpenCodeWorkerConfig.parse(value)


__all__ = [
    "KNOWN_EVENT_TYPES",
    "KNOWN_PART_TYPES",
    "KNOWN_TOOL_STATUSES",
    "OpenCodeChildSession",
    "OpenCodeError",
    "OpenCodeEventType",
    "OpenCodeMessage",
    "OpenCodeMessageInfo",
    "OpenCodeModelReference",
    "OpenCodePart",
    "OpenCodePartType",
    "OpenCodePermissionRequest",
    "OpenCodeRunEvent",
    "OpenCodeSessionExport",
    "OpenCodeSessionInfo",
    "OpenCodeShapeError",
    "OpenCodeTokenUsage",
    "OpenCodeToolState",
    "OpenCodeToolStatus",
    "OpenCodeWorkerConfig",
    "UnknownOpenCodeShapeError",
    "parse_child_sessions",
    "parse_model_reference",
    "parse_run_event",
    "parse_run_events",
    "parse_run_jsonl",
    "parse_session_export",
    "parse_worker_config",
]

"""A typed client over the ``/api/*`` operations the OpenCode 2 binding admits. It never calls an
``/api/experimental/*`` operation, and its errors and reprs never carry the registered password."""

from __future__ import annotations

import base64
import json
from collections.abc import Generator, Iterator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from enum import StrEnum
from urllib.parse import quote, urlencode

from blizzard.foundation.roles import domain_model
from blizzard.runner.harness.json_value import JsonObject, JsonValue, decode_json
from blizzard.runner.harness.opencode_v2.registration import ServiceRegistration
from blizzard.runner.harness.opencode_v2.transport import IServiceTransport, ServiceRequest

API_PREFIX = "/api/"
EXPERIMENTAL_PREFIX = "/api/experimental/"
SERVICE_USERNAME = "opencode"
#: The largest page the service serves; it defaults to 50.
MESSAGE_PAGE_LIMIT = 200


class ServiceRequestError(RuntimeError):
    """The service answered an operation with a status other than success."""

    def __init__(self, operation: str, status: int, detail: str) -> None:
        super().__init__(f"OpenCode {operation} answered {status}: {detail}")
        self.operation = operation
        self.status = status
        self.detail = detail


class ServiceShapeError(ValueError):
    """The service answered with a shape the client does not recognize."""


class PermissionEffect(StrEnum):
    ALLOW = "allow"
    DENY = "deny"
    ASK = "ask"


class PermissionDecision(StrEnum):
    ONCE = "once"
    ALWAYS = "always"
    REJECT = "reject"


class Delivery(StrEnum):
    QUEUE = "queue"
    STEER = "steer"


@domain_model
@dataclass(frozen=True)
class ModelReference:
    provider_id: str
    id: str
    variant: str | None = None

    def payload(self) -> JsonObject:
        body: JsonObject = {"providerID": self.provider_id, "id": self.id}
        if self.variant is not None:
            body["variant"] = self.variant
        return body


@domain_model
@dataclass(frozen=True)
class PermissionRule:
    action: str
    resource: str
    effect: PermissionEffect

    def payload(self) -> JsonObject:
        return {"action": self.action, "resource": self.resource, "effect": self.effect.value}


@domain_model
@dataclass(frozen=True)
class SessionCreate:
    """What a caller-named session is created with."""

    id: str
    directory: str
    metadata: Mapping[str, JsonValue]
    rules: Sequence[PermissionRule] = ()
    model: ModelReference | None = None
    title: str | None = None
    agent: str | None = None

    def payload(self) -> JsonObject:
        body: JsonObject = {
            "id": self.id,
            "location": {"directory": self.directory},
            "metadata": dict(self.metadata),
            "permissions": [rule.payload() for rule in self.rules],
        }
        if self.model is not None:
            body["model"] = self.model.payload()
        if self.title is not None:
            body["title"] = self.title
        if self.agent is not None:
            body["agent"] = self.agent
        return body


@domain_model
@dataclass(frozen=True)
class MessagePage:
    """One page of a message listing; ``next`` resumes after its last message, and ``None`` on an empty page."""

    items: tuple[JsonObject, ...]
    next: str | None
    raw: JsonObject


@domain_model
@dataclass(frozen=True)
class ServiceInfo:
    version: str
    pid: int


def _object(value: JsonValue, operation: str) -> JsonObject:
    if not isinstance(value, dict):
        raise ServiceShapeError(f"OpenCode {operation} did not answer an object")
    return value


def _data(value: JsonValue, operation: str) -> JsonValue:
    return _object(value, operation).get("data")


def _objects(value: JsonValue, operation: str) -> list[JsonObject]:
    if not isinstance(value, list):
        raise ServiceShapeError(f"OpenCode {operation} did not answer a list")
    return [_object(item, operation) for item in value]


def parse_message_page(answer: JsonValue) -> MessagePage:
    """One message listing's answer: its messages, the cursor after them, and the envelope as served."""
    operation = "session.message.list"
    envelope = _object(answer, operation)
    page_cursor = envelope.get("cursor")
    following = page_cursor.get("next") if isinstance(page_cursor, dict) else None
    if following is not None and not isinstance(following, str):
        raise ServiceShapeError(f"OpenCode {operation} answered a cursor that is not a string")
    return MessagePage(tuple(_objects(envelope.get("data"), operation)), following, envelope)


def basic_authorization(password: str) -> str:
    token = base64.b64encode(f"{SERVICE_USERNAME}:{password}".encode()).decode()
    return f"Basic {token}"


class OpenCodeServiceClient:
    """Operations on one registered service. Each returns the decoded ``data`` it answers; the shapes inside are the
    corpus's to pin, not this client's."""

    def __init__(self, registration: ServiceRegistration, transport: IServiceTransport) -> None:
        self._registration = registration
        self._transport = transport

    @property
    def registration(self) -> ServiceRegistration:
        return self._registration

    def _request(
        self, method: str, path: str, body: JsonValue | None = None, *, timeout_seconds: float = 30.0
    ) -> ServiceRequest:
        if not path.startswith(API_PREFIX) or path.startswith(EXPERIMENTAL_PREFIX):
            raise ValueError(f"the OpenCode 2 client calls only non-experimental /api/* operations, not {path!r}")
        headers = {"authorization": basic_authorization(self._registration.password), "accept": "application/json"}
        payload = None
        if body is not None:
            payload = json.dumps(body).encode()
            headers["content-type"] = "application/json"
        return ServiceRequest(method, self._registration.url + path, headers, payload, timeout_seconds)

    def _call(self, operation: str, method: str, path: str, body: JsonValue | None = None) -> JsonValue:
        response = self._transport.send(self._request(method, path, body))
        if not 200 <= response.status < 300:
            detail = response.body.decode(errors="replace")[:300]
            raise ServiceRequestError(operation, response.status, detail)
        if not response.body:
            return None
        try:
            return decode_json(response.body)
        except ValueError as exc:
            raise ServiceShapeError(f"OpenCode {operation} did not answer JSON") from exc

    @staticmethod
    def _session(session_id: str, suffix: str = "") -> str:
        return f"/api/session/{quote(session_id, safe='')}{suffix}"

    def info(self) -> ServiceInfo:
        answer = _object(self._call("server.info", "GET", "/api/info"), "server.info")
        version, pid = answer.get("version"), answer.get("pid")
        if not isinstance(version, str) or not isinstance(pid, int) or isinstance(pid, bool):
            raise ServiceShapeError("OpenCode server.info lacks a version or pid")
        return ServiceInfo(version=version, pid=pid)

    def models(self) -> list[JsonObject]:
        return _objects(_data(self._call("model.list", "GET", "/api/model"), "model.list"), "model.list")

    def create_session(self, spec: SessionCreate) -> JsonObject:
        return _object(
            _data(self._call("session.create", "POST", "/api/session", spec.payload()), "session.create"),
            "session.create",
        )

    def session(self, session_id: str) -> JsonObject:
        answer = self._call("session.get", "GET", self._session(session_id))
        return _object(_data(answer, "session.get"), "session.get")

    def update_session(
        self,
        session_id: str,
        *,
        rules: Sequence[PermissionRule] | None = None,
        metadata: Mapping[str, JsonValue] | None = None,
    ) -> None:
        """Replace the session's rules or metadata; the service answers with no body."""
        body: JsonObject = {}
        if rules is not None:
            body["permissions"] = [rule.payload() for rule in rules]
        if metadata is not None:
            body["metadata"] = dict(metadata)
        self._call("session.update", "PATCH", self._session(session_id), body)

    def remove_session(self, session_id: str) -> None:
        self._call("session.remove", "DELETE", self._session(session_id))

    def children(self, parent_id: str) -> list[JsonObject]:
        query = urlencode({"parentID": parent_id})
        return _objects(
            _data(self._call("session.list", "GET", f"/api/session?{query}"), "session.list"), "session.list"
        )

    def running_sessions(self) -> frozenset[str]:
        active = _object(
            _data(self._call("session.active", "GET", "/api/session/active"), "session.active"), "session.active"
        )
        return frozenset(active)

    def set_environment(self, session_id: str, variables: Mapping[str, str]) -> None:
        body: JsonObject = {"variables": dict(variables)}
        self._call("session.environment", "PUT", self._session(session_id, "/environment"), body)

    def prompt(self, session_id: str, text: str, delivery: Delivery | None = None) -> JsonObject:
        body: JsonObject = {"text": text}
        if delivery is not None:
            body["delivery"] = delivery.value
        answer = self._call("session.prompt", "POST", self._session(session_id, "/prompt"), body)
        return _object(_data(answer, "session.prompt"), "session.prompt")

    def interrupt(self, session_id: str) -> bool:
        answer = _object(
            self._call("session.interrupt", "POST", self._session(session_id, "/interrupt")), "session.interrupt"
        )
        return answer.get("interrupted") is True

    def compact(self, session_id: str) -> None:
        self._call("session.compact", "POST", self._session(session_id, "/compact"), {})

    def switch_model(self, session_id: str, model: ModelReference) -> None:
        self._call("session.switchModel", "POST", self._session(session_id, "/model"), {"model": model.payload()})

    def switch_agent(self, session_id: str, agent: str) -> None:
        self._call("session.switchAgent", "POST", self._session(session_id, "/agent"), {"agent": agent})

    def message_page(
        self, session_id: str, *, cursor: str | None = None, order: str = "asc", limit: int | None = None
    ) -> MessagePage:
        """One page of ``session_id``'s messages: from the start in ``order``, or after ``cursor``, which carries its
        own order and is never sent beside one."""
        params: dict[str, str] = {"cursor": cursor} if cursor is not None else {"order": order}
        if limit is not None:
            params["limit"] = str(limit)
        path = self._session(session_id, f"/message?{urlencode(params)}")
        return parse_message_page(self._call("session.message.list", "GET", path))

    def messages(self, session_id: str, *, order: str = "asc") -> list[JsonObject]:
        """Every message of ``session_id`` in ``order``, following the service's pages to the last."""
        page = self.message_page(session_id, order=order, limit=MESSAGE_PAGE_LIMIT)
        listed = list(page.items)
        # A page shorter than asked is taken as the last; 2.1.4 proves only that an empty page ends the listing.
        while page.next is not None and len(page.items) == MESSAGE_PAGE_LIMIT:
            page = self.message_page(session_id, cursor=page.next, limit=MESSAGE_PAGE_LIMIT)
            listed.extend(page.items)
        return listed

    def message(self, session_id: str, message_id: str) -> JsonObject:
        path = self._session(session_id, f"/message/{quote(message_id, safe='')}")
        return _object(
            _data(self._call("session.message.get", "GET", path), "session.message.get"), "session.message.get"
        )

    def inbox(self, session_id: str) -> list[JsonObject]:
        answer = self._call("session.inbox.list", "GET", self._session(session_id, "/inbox"))
        return _objects(_data(answer, "session.inbox.list"), "session.inbox.list")

    def permission_requests(self, session_id: str) -> list[JsonObject]:
        answer = self._call("session.permission.list", "GET", self._session(session_id, "/permission"))
        return _objects(_data(answer, "session.permission.list"), "session.permission.list")

    def reply_permission(self, session_id: str, request_id: str, decision: PermissionDecision) -> None:
        path = self._session(session_id, f"/permission/{quote(request_id, safe='')}/reply")
        self._call("session.permission.reply", "POST", path, {"decision": decision.value})

    def forms(self, session_id: str) -> list[JsonObject]:
        answer = self._call("session.form.list", "GET", self._session(session_id, "/form"))
        return _objects(_data(answer, "session.form.list"), "session.form.list")

    def create_form(self, session_id: str, title: str, fields: Sequence[JsonObject]) -> JsonObject:
        body: JsonObject = {"title": title, "fields": list(fields)}
        answer = self._call("session.form.create", "POST", self._session(session_id, "/form"), body)
        return _object(_data(answer, "session.form.create"), "session.form.create")

    def cancel_form(self, session_id: str, form_id: str) -> None:
        path = self._session(session_id, f"/form/{quote(form_id, safe='')}")
        self._call("session.form.cancel", "DELETE", path)

    @contextmanager
    def events(self, timeout_seconds: float = 300.0) -> Generator[Iterator[JsonObject]]:
        """The event stream's events while it stays open. It is volatile by contract: a prompt to look, never the
        record."""
        request = self._request("GET", "/api/event", timeout_seconds=timeout_seconds)
        with self._transport.stream(request) as lines:
            yield _sse_events(lines)


def _sse_events(lines: Iterator[bytes]) -> Iterator[JsonObject]:
    for line in lines:
        if not line.startswith(b"data:"):
            continue
        try:
            decoded = decode_json(line[len(b"data:") :].strip())
        except ValueError:
            continue
        if isinstance(decoded, dict):
            yield decoded


__all__ = [
    "MESSAGE_PAGE_LIMIT",
    "Delivery",
    "MessagePage",
    "ModelReference",
    "OpenCodeServiceClient",
    "PermissionDecision",
    "PermissionEffect",
    "PermissionRule",
    "ServiceInfo",
    "ServiceRequestError",
    "ServiceShapeError",
    "SessionCreate",
    "basic_authorization",
    "parse_message_page",
]

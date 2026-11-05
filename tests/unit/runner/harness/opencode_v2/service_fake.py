"""An in-memory OpenCode 2 service with sessions, messages, permission and form requests, inbox, and a model listing,
for driving a turn without a process. A prompt runs ``on_prompt``, a permission reply ``on_reply``, and each identity
check — one per driver poll — ``on_poll``; each may move the session tree to its next state."""

from __future__ import annotations

import base64
import json
from collections.abc import Callable, Generator, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import parse_qs, urlsplit

from blizzard.runner.harness.opencode_v2.registration import ServiceRegistration
from blizzard.runner.harness.opencode_v2.transport import ServiceRequest, ServiceResponse, ServiceUnreachableError

REGISTRATION = ServiceRegistration(
    id="svc-1", version="2.1.4", url="http://127.0.0.1:49374", pid=100, password="s3cret-password"
)
type Record = dict[str, Any]

TOKENS: Record = {"input": 100, "output": 10, "reasoning": 5, "cache": {"read": 50, "write": 0}}
MODEL: Record = {"providerID": "openai", "id": "gpt-6-luna", "variant": "high"}

type Hook = Callable[["SessionService", str, Record], None]


def assistant(message_id: str, text: str = "", *, tools: tuple[Record, ...] = (), **extra: object) -> Record:
    content: list[Record] = [*tools]
    if text:
        content.append({"type": "text", "text": text})
    return {
        "type": "assistant",
        "id": message_id,
        "time": {"created": 2, "completed": 3},
        "content": content,
        "tokens": TOKENS,
        "cost": 0.25,
        "finish": "stop",
        "model": MODEL,
        **extra,
    }


def tool(part_id: str, status: str = "completed", **state: object) -> Record:
    return {"type": "tool", "id": part_id, "name": "shell", "state": {"status": status, **state}}


def idle(message_id: str, outcome: str = "succeeded", error: Record | None = None) -> Record:
    record: Record = {"type": "idle", "id": message_id, "time": {"created": 3}, "outcome": outcome}
    if error is not None:
        record["error"] = error
    return record


@dataclass
class SessionService:
    pid: int = 100
    version: str = "2.1.4"
    sessions: dict[str, Record] = field(default_factory=dict[str, Record])
    messages: dict[str, list[Record]] = field(default_factory=dict[str, list[Record]])
    environments: dict[str, dict[str, str]] = field(default_factory=dict[str, dict[str, str]])
    running: set[str] = field(default_factory=set[str])
    inbox: dict[str, list[Record]] = field(default_factory=dict[str, list[Record]])
    permissions: dict[str, list[Record]] = field(default_factory=dict[str, list[Record]])
    forms: dict[str, list[Record]] = field(default_factory=dict[str, list[Record]])
    replies: list[tuple[str, str, str]] = field(default_factory=list[tuple[str, str, str]])
    interrupted: list[str] = field(default_factory=list[str])
    compactions: list[str] = field(default_factory=list[str])
    switched: list[tuple[str, object]] = field(default_factory=list[tuple[str, object]])
    prompts: list[tuple[str, Record]] = field(default_factory=list[tuple[str, Record]])
    models: list[Record] = field(
        default_factory=lambda: [
            {"providerID": "openai", "id": "gpt-6-luna", "variants": [{"id": "low"}, {"id": "high"}]}
        ]
    )
    on_prompt: Hook | None = None
    on_reply: Hook | None = None
    polls: int = 0
    #: A path suffix -> the status every request ending in it answers instead.
    failing: dict[str, int] = field(default_factory=dict[str, int])
    on_poll: Callable[[SessionService], None] | None = None
    #: Each message listing's session and query, in order.
    listings: list[tuple[str, dict[str, str]]] = field(default_factory=list[tuple[str, dict[str, str]]])

    def _page(self, session_id: str, query: dict[str, list[str]]) -> tuple[int, object]:
        """A page as 2.1.4 serves one: a cursor carries its own order and is refused beside one."""
        cursor, order = query.get("cursor", [None])[0], query.get("order", [None])[0]
        if cursor is not None and order is not None:
            return 400, {"_tag": "InvalidCursorError", "message": "Cursor cannot be combined with order"}
        limit = int(query.get("limit", ["50"])[0])
        if limit > 200:
            return 400, {"_tag": "InvalidRequestError"}
        listed = list(self.messages[session_id])
        after = None
        if cursor is not None:
            decoded = json.loads(base64.urlsafe_b64decode(cursor + "=" * (-len(cursor) % 4)))
            after, order = decoded["id"], decoded["order"]
        if order == "desc":
            listed.reverse()
        if after is not None:
            ids = [m["id"] for m in listed]
            listed = listed[ids.index(after) + 1 :] if after in ids else []
        page = listed[:limit]
        if not page:
            return 200, {"data": [], "cursor": {"previous": None, "next": None}}
        following = {"id": page[-1]["id"], "order": order or "asc", "direction": "next"}
        token = base64.urlsafe_b64encode(json.dumps(following).encode()).decode().rstrip("=")
        return 200, {"data": page, "cursor": {"previous": None, "next": token}}

    def add_session(self, session_id: str, *, parent: str | None = None, **fields: object) -> Record:
        record: Record = {
            "id": session_id,
            "location": {"directory": "/work"},
            "metadata": {},
            "permissions": [],
            "tokens": TOKENS,
            "cost": 0,
            **fields,
        }
        if parent is not None:
            record["parentID"] = parent
        self.sessions[session_id] = record
        self.messages.setdefault(session_id, [])
        return record

    def answer(self, request: ServiceRequest) -> tuple[int, object]:
        parts = urlsplit(request.url)
        path, query = parts.path, parse_qs(parts.query)
        body: Record = json.loads(request.body) if request.body else {}
        segments = path.split("/")[3:]
        method = request.method
        for suffix, status in self.failing.items():
            if path.endswith(suffix):
                return status, {"error": "failing"}
        if path == "/api/info":
            if self.on_poll is not None:
                self.polls += 1
                self.on_poll(self)
            return 200, {"version": self.version, "pid": self.pid}
        if path == "/api/model":
            return 200, {"data": self.models}
        if path == "/api/session/active":
            return 200, {"data": {sid: {"type": "running"} for sid in self.running}}
        if path == "/api/session" and method == "POST":
            record = self.add_session(
                body["id"],
                location=body["location"],
                metadata=body["metadata"],
                permissions=body["permissions"],
                **({"model": body["model"]} if "model" in body else {}),
            )
            return 200, {"data": record}
        if path == "/api/session" and method == "GET":
            parent = query.get("parentID", [""])[0]
            return 200, {"data": [s for s in self.sessions.values() if s.get("parentID") == parent]}
        session_id = segments[0]
        if session_id not in self.sessions:
            return 404, {"_tag": "SessionNotFoundError", "sessionID": session_id, "message": "Session not found"}
        rest = segments[1:]
        if not rest:
            if method == "GET":
                return 200, {"data": self.sessions[session_id]}
            if method == "PATCH":
                self.sessions[session_id].update(body)
                return 204, None
        action = rest[0]
        if action == "environment":
            self.environments[session_id] = body["variables"]
            return 204, None
        if action == "prompt":
            self.prompts.append((session_id, body))
            if self.on_prompt is not None:
                self.on_prompt(self, session_id, body)
            return 200, {"data": {"id": "msg_prompt"}}
        if action == "message" and len(rest) == 2:
            found = [m for m in self.messages[session_id] if m["id"] == rest[1]]
            return (200, {"data": found[0]}) if found else (404, {"error": "message not found"})
        if action == "message":
            self.listings.append((session_id, {key: values[-1] for key, values in query.items()}))
            return self._page(session_id, query)
        if action == "inbox":
            return 200, {"data": self.inbox.get(session_id, [])}
        if action == "interrupt":
            self.interrupted.append(session_id)
            self.running.discard(session_id)
            return 200, {"interrupted": True}
        if action == "compact":
            self.compactions.append(session_id)
            return 204, None
        if action in ("model", "agent"):
            self.switched.append((action, body[action]))
            return 204, None
        if action == "permission" and len(rest) == 1:
            return 200, {"data": self.permissions.get(session_id, [])}
        if action == "permission":
            request_id, decision = rest[1], body["decision"]
            self.permissions[session_id] = [p for p in self.permissions.get(session_id, []) if p["id"] != request_id]
            self.replies.append((session_id, request_id, decision))
            if self.on_reply is not None:
                self.on_reply(self, session_id, {"request": request_id, "decision": decision})
            return 204, None
        if action == "form" and len(rest) == 1:
            return 200, {"data": self.forms.get(session_id, [])}
        if action == "form" and method == "DELETE":
            self.forms[session_id] = [f for f in self.forms.get(session_id, []) if f["id"] != rest[1]]
            self.replies.append((session_id, rest[1], "cancel"))
            return 204, None
        return 404, {"error": "no route"}


@dataclass
class SessionTransport:
    """Sends to ``service``; ``None`` there is a service that refuses connections. The event stream ends at once."""

    service: SessionService | None
    requests: list[ServiceRequest] = field(default_factory=list[ServiceRequest])

    def send(self, request: ServiceRequest) -> ServiceResponse:
        self.requests.append(request)
        if self.service is None:
            raise ServiceUnreachableError("connection refused")
        status, body = self.service.answer(request)
        return ServiceResponse(status, b"" if body is None else json.dumps(body).encode())

    @contextmanager
    def stream(self, request: ServiceRequest) -> Generator[Iterator[bytes]]:
        self.requests.append(request)
        if self.service is None:
            raise ServiceUnreachableError("connection refused")
        yield iter(())

    def calls(self, method: str, suffix: str) -> list[ServiceRequest]:
        return [r for r in self.requests if r.method == method and urlsplit(r.url).path.endswith(suffix)]

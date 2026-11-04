"""An in-memory OpenCode 2 service for client and driver unit tests: routes answer by method and path, and every
request is recorded."""

from __future__ import annotations

import json
from collections.abc import Callable, Generator, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from urllib.parse import urlsplit

from blizzard.runner.harness.json_value import JsonValue
from blizzard.runner.harness.opencode_v2.registration import ServiceRegistration
from blizzard.runner.harness.opencode_v2.transport import ServiceRequest, ServiceResponse, ServiceUnreachableError

type Route = Callable[[ServiceRequest], tuple[int, JsonValue]]

REGISTRATION = ServiceRegistration(
    id="svc-1", version="2.1.4", url="http://127.0.0.1:49374", pid=100, password="s3cret-password"
)


@dataclass
class FakeService:
    """One service lifetime: ``pid`` and ``version`` are what ``/api/info`` reports."""

    pid: int = 100
    version: str = "2.1.4"
    routes: dict[tuple[str, str], Route] = field(default_factory=dict[tuple[str, str], Route])
    running: set[str] = field(default_factory=set[str])
    children: dict[str, list[str]] = field(default_factory=dict[str, list[str]])
    inbox: dict[str, int] = field(default_factory=dict[str, int])
    interrupted: list[str] = field(default_factory=list[str])

    def answer(self, request: ServiceRequest) -> tuple[int, JsonValue]:
        parts = urlsplit(request.url)
        key = (request.method, parts.path)
        if key in self.routes:
            return self.routes[key](request)
        return self._default(request.method, parts.path, parts.query)

    def _default(self, method: str, path: str, query: str) -> tuple[int, JsonValue]:
        if (method, path) == ("GET", "/api/info"):
            return 200, {"version": self.version, "pid": self.pid, "urls": [], "paths": {"tmp": "/tmp"}}
        if (method, path) == ("GET", "/api/session/active"):
            return 200, {"data": {sid: {"type": "running"} for sid in self.running}}
        if (method, path) == ("GET", "/api/session"):
            parent = query.removeprefix("parentID=")
            return 200, {"data": [{"id": child, "parentID": parent} for child in self.children.get(parent, [])]}
        segments = path.split("/")
        if method == "GET" and path.endswith("/inbox"):
            return 200, {"data": [{"id": f"msg_{n}"} for n in range(self.inbox.get(segments[3], 0))]}
        if method == "POST" and path.endswith("/interrupt"):
            self.interrupted.append(segments[3])
            self.running.discard(segments[3])
            return 200, {"interrupted": True}
        return 404, {"error": "no route"}


@dataclass
class FakeTransport:
    """Sends to ``service``; ``None`` there is a service that refuses connections."""

    service: FakeService | None
    requests: list[ServiceRequest] = field(default_factory=list[ServiceRequest])
    stream_lines: list[bytes] = field(default_factory=list[bytes])

    def send(self, request: ServiceRequest) -> ServiceResponse:
        self.requests.append(request)
        if self.service is None:
            raise ServiceUnreachableError("connection refused")
        status, body = self.service.answer(request)
        return ServiceResponse(status, b"" if body is None else json.dumps(body).encode())

    @contextmanager
    def stream(self, request: ServiceRequest) -> Generator[Iterator[bytes]]:
        self.requests.append(request)
        yield iter(self.stream_lines)

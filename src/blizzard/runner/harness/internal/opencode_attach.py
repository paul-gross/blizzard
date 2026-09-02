"""A local request observer for OpenCode's interactive attach path."""

from __future__ import annotations

import contextlib
import http.client
import json
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, replace
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Protocol
from urllib.parse import urlsplit

from blizzard.runner.harness.internal.opencode_loopback import (
    ILoopbackTransport,
    LoopbackRequest,
    LoopbackResponse,
    LoopbackTransportError,
)

_EVENT_PATHS = ("/global/event", "/event")
_MAX_BUFFERED_RESPONSE_BYTES = 1024 * 1024
_ATTACH_READY_QUIET_SECONDS = 0.25
_STREAM_HOP_BY_HOP_HEADERS = frozenset(
    {
        "connection",
        "content-length",
        "keep-alive",
        "proxy-authenticate",
        "proxy-authorization",
        "te",
        "trailer",
        "transfer-encoding",
        "upgrade",
        "via",
    }
)


@dataclass(frozen=True)
class OpenCodeAttachRequest:
    """One request made by the attached client, without retaining its body."""

    method: str
    path: str
    status: int | None
    session_matches: bool = False
    directory_matches: bool = False
    content_type: str | None = None
    event_stream_valid: bool = False
    event_stream_bytes: int = 0


@dataclass(frozen=True)
class OpenCodeAttachSignal:
    """The server-side signals that the interactive client attached to the requested session."""

    requests: tuple[OpenCodeAttachRequest, ...]
    session_matches: bool
    directory_matches: bool
    client_alive_after_handshake: bool = False
    continuation_observed: bool = False

    @property
    def session_status(self) -> int | None:
        return _first_status(self.requests, "/session/")

    @property
    def event_status(self) -> int | None:
        for path in _EVENT_PATHS:
            status = _first_status(self.requests, path)
            if status is not None:
                return status
        return None

    @property
    def observed(self) -> bool:
        return (
            self.session_matches
            and self.directory_matches
            and _successful(self.session_status)
            and _successful(self.event_status)
            and self.event_stream_valid
            and self.client_alive_after_handshake
            and self.continuation_observed
        )

    @property
    def event_stream_valid(self) -> bool:
        return any(request.path in _EVENT_PATHS and request.event_stream_valid for request in self.requests)

    @property
    def event_stream_bytes(self) -> int:
        return max(
            (request.event_stream_bytes for request in self.requests if request.path in _EVENT_PATHS),
            default=0,
        )

    @property
    def handshake_complete(self) -> bool:
        """Return whether attach reached the session and a validated upstream SSE handshake."""

        return _successful(self.session_status) and _successful(self.event_status) and self.event_stream_valid


def _successful(status: int | None) -> bool:
    return status is not None and 200 <= status < 300


def _first_status(requests: tuple[OpenCodeAttachRequest, ...], path_prefix: str) -> int | None:
    for request in requests:
        if request.path == path_prefix or request.path.startswith(path_prefix):
            return request.status
    return None


class OpenCodeAttachProxy:
    """Forward attach traffic while validating and preserving OpenCode's upstream SSE stream."""

    def __init__(
        self,
        target_url: str,
        *,
        session_id: str,
        directory: Path,
        transport: ILoopbackTransport,
        timeout_seconds: float = 10.0,
    ) -> None:
        self._target_url = target_url.rstrip("/")
        self._session_id = session_id
        self._directory = directory
        self._transport = transport
        self._timeout_seconds = timeout_seconds
        self._requests: list[OpenCodeAttachRequest] = []
        self._active_responses: list[LoopbackResponse] = []
        self._active_handlers: set[threading.Thread] = set()
        self._lock = threading.Lock()
        self._signal = threading.Event()
        self._closed = threading.Event()
        self._server: ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None

    @property
    def url(self) -> str:
        if self._server is None:
            raise RuntimeError("the OpenCode attach proxy is not running")
        return f"http://127.0.0.1:{self._server.server_port}"

    def start(self) -> None:
        if self._server is not None:
            raise RuntimeError("the OpenCode attach proxy is already running")
        owner = self

        class Handler(BaseHTTPRequestHandler):
            def do_DELETE(self) -> None:
                owner._forward(self)

            def do_GET(self) -> None:
                owner._forward(self)

            def do_PATCH(self) -> None:
                owner._forward(self)

            def do_POST(self) -> None:
                owner._forward(self)

            def do_PUT(self) -> None:
                owner._forward(self)

            def log_message(self, format: str, *args: object) -> None:
                del format, args

        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        server.daemon_threads = True
        self._server = server
        self._thread = threading.Thread(target=server.serve_forever, name="blizzard-opencode-attach-proxy", daemon=True)
        self._thread.start()

    def wait_for_attachment(
        self,
        timeout_seconds: float,
        process_alive: Callable[[], bool],
        on_handshake: Callable[[], None] | None = None,
    ) -> OpenCodeAttachSignal:
        """Wait for a live SSE handshake and let attach startup requests settle before input."""

        deadline = time.monotonic() + timeout_seconds
        ready_deadline: float | None = None
        request_count: int | None = None
        while time.monotonic() < deadline and process_alive():
            signal = self.signal()
            if signal.handshake_complete:
                if not process_alive():
                    return self._with_client_liveness(signal, False)
                current_request_count = len(signal.requests)
                if current_request_count != request_count:
                    request_count = current_request_count
                    ready_deadline = min(deadline, time.monotonic() + _ATTACH_READY_QUIET_SECONDS)
                elif ready_deadline is not None and time.monotonic() >= ready_deadline:
                    if on_handshake is not None:
                        on_handshake()
                    return self._with_client_liveness(signal, process_alive())
            self._signal.wait(0.05)
            self._signal.clear()
        return self._with_client_liveness(self.signal(), False)

    def wait_for_event_request(self, timeout_seconds: float, process_alive: Callable[[], bool]) -> bool:
        """Wait until the attached client has opened its event-stream request."""

        deadline = time.monotonic() + timeout_seconds
        while time.monotonic() < deadline and process_alive():
            if any(request.path in _EVENT_PATHS for request in self.signal().requests):
                return True
            self._signal.wait(0.05)
            self._signal.clear()
        return False

    @staticmethod
    def _with_client_liveness(signal: OpenCodeAttachSignal, alive: bool) -> OpenCodeAttachSignal:
        return replace(signal, client_alive_after_handshake=alive)

    def signal(self) -> OpenCodeAttachSignal:
        with self._lock:
            requests = tuple(self._requests)
        session_matches = any(request.session_matches for request in requests)
        directory_matches = any(request.directory_matches for request in requests)
        return OpenCodeAttachSignal(requests, session_matches, directory_matches)

    def close(self) -> OpenCodeAttachSignal:
        self._closed.set()
        with self._lock:
            active_responses = tuple(self._active_responses)
        for response in active_responses:
            with contextlib.suppress(OSError):
                response.close()
        server = self._server
        if server is not None:
            server.shutdown()
            server.server_close()
        thread = self._thread
        if thread is not None:
            thread.join(timeout=1.0)
        self._wait_for_handlers()
        return self.signal()

    def _forward(self, handler: BaseHTTPRequestHandler) -> None:
        current = threading.current_thread()
        with self._lock:
            self._active_handlers.add(current)
        try:
            self._forward_request(handler)
        finally:
            with self._lock:
                self._active_handlers.discard(current)
            self._signal.set()

    def _forward_request(self, handler: BaseHTTPRequestHandler) -> None:
        path = urlsplit(handler.path).path
        request_index = self._record_request(OpenCodeAttachRequest(handler.command, path, None))
        target = f"{self._target_url}{handler.path}"
        length = int(handler.headers.get("Content-Length", "0"))
        body = handler.rfile.read(length) if length else None
        headers = {
            name: value
            for name, value in handler.headers.items()
            if name.lower() not in {"connection", "content-length", "host", "transfer-encoding"}
        }
        request = LoopbackRequest(method=handler.command, url=target, headers=headers, body=body)
        try:
            with self._transport.request(request, timeout=self._timeout_seconds) as response:
                self._track_response(response)
                try:
                    if path in _EVENT_PATHS:
                        self._forward_event(handler, path, response, request_index)
                    else:
                        self._forward_buffered(handler, path, response, request_index)
                finally:
                    self._untrack_response(response)
        except (LoopbackTransportError, TimeoutError, OSError):
            with contextlib.suppress(OSError):
                handler.send_error(502)

    def _wait_for_handlers(self) -> None:
        deadline = time.monotonic() + 1.0
        while time.monotonic() < deadline:
            with self._lock:
                handlers = tuple(self._active_handlers)
            if not handlers:
                return
            remaining = max(0.0, deadline - time.monotonic())
            for handler in handlers:
                handler.join(timeout=min(0.05, remaining))

    def _forward_buffered(
        self,
        handler: BaseHTTPRequestHandler,
        path: str,
        response: LoopbackResponse,
        request_index: int,
    ) -> None:
        response_body = response.read(_MAX_BUFFERED_RESPONSE_BYTES)
        session_matches = False
        directory_matches = False
        if path == f"/session/{self._session_id}":
            session_matches, directory_matches = self._session_shape(response_body)
        self._update_request(
            request_index,
            status=response.status,
            session_matches=session_matches,
            directory_matches=directory_matches,
            content_type=response.header("Content-Type"),
        )
        self._send_buffered_response(handler, response.status, response.header_items(), response_body)

    def _forward_event(
        self,
        handler: BaseHTTPRequestHandler,
        path: str,
        response: LoopbackResponse,
        request_index: int,
    ) -> None:
        content_type = response.header("Content-Type")
        valid_content_type = _is_sse_content_type(content_type)
        if not (200 <= response.status < 300 and valid_content_type):
            body = response.read(_MAX_BUFFERED_RESPONSE_BYTES)
            self._update_request(
                request_index,
                status=response.status,
                content_type=content_type,
                event_stream_bytes=len(body),
            )
            self._send_buffered_response(handler, response.status, response.header_items(), body)
            return

        try:
            handler.send_response(response.status)
            for name, value in response.header_items():
                if name.lower() not in _STREAM_HOP_BY_HOP_HEADERS:
                    handler.send_header(name, value)
            handler.end_headers()
        except OSError:
            return

        # The response headers are the SSE handshake, and OpenCode's global stream may stay idle
        # after it; a stream ending before a complete frame is invalidated after the fact instead.
        self._update_request(
            request_index,
            status=response.status,
            content_type=content_type,
            event_stream_valid=True,
        )
        stream_bytes = 0
        stream_buffer = b""
        frame_observed = False
        upstream_ended = False
        upstream_failed = False
        while not self._closed.is_set():
            try:
                chunk = response.read_chunk()
            except (http.client.HTTPException, LoopbackTransportError, TimeoutError, OSError):
                upstream_failed = not self._closed.is_set()
                break
            if not chunk:
                upstream_ended = not self._closed.is_set()
                break
            stream_bytes += len(chunk)
            stream_buffer = (stream_buffer + chunk)[-8192:]
            if not frame_observed and _contains_sse_frame(stream_buffer):
                frame_observed = True
            self._update_request(request_index, event_stream_bytes=stream_bytes)
            try:
                handler.wfile.write(chunk)
                handler.wfile.flush()
            except OSError:
                break
        if (upstream_ended or upstream_failed) and not frame_observed:
            self._update_request(request_index, event_stream_valid=False, event_stream_bytes=stream_bytes)
        else:
            self._update_request(request_index, event_stream_bytes=stream_bytes)

    def _send_buffered_response(
        self,
        handler: BaseHTTPRequestHandler,
        status: int,
        headers: tuple[tuple[str, str], ...],
        body: bytes,
    ) -> None:
        try:
            handler.send_response(status)
            sent_content_type = False
            for name, value in headers:
                if name.lower() in _STREAM_HOP_BY_HOP_HEADERS:
                    continue
                if name.lower() == "content-type":
                    sent_content_type = True
                handler.send_header(name, value)
            if not sent_content_type:
                handler.send_header("Content-Type", "application/json")
            handler.send_header("Content-Length", str(len(body)))
            handler.end_headers()
            if body:
                handler.wfile.write(body)
                handler.wfile.flush()
        except OSError:
            return

    def _record_request(self, request: OpenCodeAttachRequest) -> int:
        with self._lock:
            index = len(self._requests)
            self._requests.append(request)
        if request.path == f"/session/{self._session_id}" or request.path in _EVENT_PATHS:
            self._signal.set()
        return index

    def _update_request(self, index: int, **changes: object) -> None:
        with self._lock:
            self._requests[index] = replace(self._requests[index], **changes)

    def _track_response(self, response: LoopbackResponse) -> None:
        with self._lock:
            self._active_responses.append(response)

    def _untrack_response(self, response: LoopbackResponse) -> None:
        with self._lock:
            if response in self._active_responses:
                self._active_responses.remove(response)

    def _session_shape(self, body: bytes) -> tuple[bool, bool]:
        try:
            payload = json.loads(body)
        except (UnicodeDecodeError, ValueError):
            return False, False
        if not isinstance(payload, dict):
            return False, False
        session_matches = payload.get("id") == self._session_id
        directory = payload.get("directory")
        if not isinstance(directory, str):
            return session_matches, False
        try:
            directory_matches = Path(directory).resolve() == self._directory.resolve()
        except OSError:
            directory_matches = False
        return session_matches, directory_matches


class IAttachProxyFactory(Protocol):
    """Builds one attach proxy once the takeover probe knows its server URL and session."""

    def __call__(
        self,
        target_url: str,
        *,
        session_id: str,
        directory: Path,
        timeout_seconds: float,
    ) -> OpenCodeAttachProxy: ...


@dataclass(frozen=True)
class LoopbackAttachProxyFactory:
    """Binds the composition root's loopback transport into every proxy the probe builds."""

    transport: ILoopbackTransport

    def __call__(
        self,
        target_url: str,
        *,
        session_id: str,
        directory: Path,
        timeout_seconds: float,
    ) -> OpenCodeAttachProxy:
        return OpenCodeAttachProxy(
            target_url,
            session_id=session_id,
            directory=directory,
            transport=self.transport,
            timeout_seconds=timeout_seconds,
        )


def _conforms_attach_proxy_factory(x: LoopbackAttachProxyFactory) -> IAttachProxyFactory:
    return x


def _is_sse_content_type(content_type: str | None) -> bool:
    if content_type is None:
        return False
    return content_type.split(";", 1)[0].strip().lower() == "text/event-stream"


def _contains_sse_frame(value: bytes) -> bool:
    normalized = value.replace(b"\r\n", b"\n").replace(b"\r", b"\n")
    for frame in normalized.split(b"\n\n")[:-1]:
        if any(line.startswith((b"data:", b"event:", b"id:", b"retry:", b":")) for line in frame.split(b"\n") if line):
            return True
    return False


__all__ = [
    "IAttachProxyFactory",
    "LoopbackAttachProxyFactory",
    "OpenCodeAttachProxy",
    "OpenCodeAttachRequest",
    "OpenCodeAttachSignal",
]

"""Injected transport for OpenCode's local control API.

The compaction request, the child-session read, and the interactive attach proxy share this seam so
none of them consults ambient proxy settings or forwards an unrelated HTTP client's credentials.
"""

from __future__ import annotations

import ipaddress
import re
import socket
import threading
import time
from collections.abc import Iterator, Mapping
from contextlib import AbstractContextManager, contextmanager, suppress
from dataclasses import dataclass, field
from typing import Any, Protocol
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener

from blizzard.runner.harness.internal.opencode_process import OpenCodeStartedProcess

LOCAL_SERVER_HOST = "127.0.0.1"
LOCAL_SERVER_START_TIMEOUT_SECONDS = 10.0
LOCAL_SERVER_POLL_SECONDS = 0.1

_LOCAL_SERVER_URL = re.compile(r"\bhttps?://(?P<host>[^\s/:]+):(?P<port>[0-9]+)\b")


def local_server_argv(binary: str) -> list[str]:
    """The argv that binds OpenCode's control API to an ephemeral loopback port."""

    return [binary, "serve", "--hostname", LOCAL_SERVER_HOST, "--port", "0"]


def wait_for_local_server(server: OpenCodeStartedProcess, timeout_seconds: float) -> str | None:
    """Read the announced listening port and address it on loopback whatever host it advertised."""

    deadline = time.monotonic() + min(timeout_seconds, LOCAL_SERVER_START_TIMEOUT_SECONDS)
    while time.monotonic() < deadline and server.poll() is None:
        line = server.read_line(LOCAL_SERVER_POLL_SECONDS)
        if not line:
            continue
        match = _LOCAL_SERVER_URL.search(line)
        if match is not None:
            return f"http://{LOCAL_SERVER_HOST}:{match.group('port')}"
    return None


_SENSITIVE_HEADERS = frozenset(
    {
        "authorization",
        "cookie",
        "proxy-authorization",
        "set-cookie",
        "x-access-token",
        "x-api-key",
        "x-auth-token",
        "x-forwarded-for",
        "x-forwarded-host",
        "x-forwarded-proto",
        "x-real-ip",
    }
)
_HOP_BY_HOP_HEADERS = frozenset(
    {
        "connection",
        "content-length",
        "host",
        "keep-alive",
        "te",
        "transfer-encoding",
        "upgrade",
        "via",
    }
)


class LoopbackTransportError(RuntimeError):
    """The local control request was invalid or could not reach its loopback server."""


@dataclass(frozen=True)
class LoopbackRequest:
    """The complete local request needed by an OpenCode control operation."""

    method: str
    url: str
    headers: Mapping[str, str] = field(default_factory=dict)
    body: bytes | None = None


class LoopbackResponse:
    """A small response surface that supports both JSON reads and SSE chunks."""

    def __init__(self, response: Any) -> None:
        self._response = response
        status = getattr(response, "status", getattr(response, "code", None))
        if not isinstance(status, int):
            raise LoopbackTransportError("the loopback response had no numeric status")
        self.status = status
        headers = getattr(response, "headers", None)
        items = headers.items() if headers is not None else ()
        self._headers = tuple(
            (name, value) for name, value in items if isinstance(name, str) and isinstance(value, str)
        )
        self._state = threading.Condition()
        self._readers = 0
        self._closed = False
        self._underlying_closed = False

    def header(self, name: str, default: str | None = None) -> str | None:
        """Return one response header without exposing the underlying urllib object."""

        normalized = name.lower()
        for header_name, value in self._headers:
            if header_name.lower() == normalized:
                return value
        return default

    def header_items(self) -> tuple[tuple[str, str], ...]:
        """Return response headers that can be copied without exposing urllib state."""

        return self._headers

    def read(self, amount: int = -1) -> bytes:
        """Read a bounded or complete response body."""

        response = self._begin_read()
        if response is None:
            return b""
        try:
            value = response.read(amount)
            if not isinstance(value, bytes):
                raise LoopbackTransportError("the loopback response body was not bytes")
            return value
        finally:
            self._end_read()

    def read_chunk(self, amount: int = 4096) -> bytes:
        """Read currently available stream data when the response supports ``read1``."""

        response = self._begin_read()
        if response is None:
            return b""
        try:
            read_one = getattr(response, "read1", None)
            value = read_one(amount) if callable(read_one) else response.read(amount)
            if not isinstance(value, bytes):
                raise LoopbackTransportError("the loopback response body was not bytes")
            return value
        finally:
            self._end_read()

    def close(self) -> None:
        """Close the underlying response, including an in-flight SSE socket."""

        with self._state:
            if self._underlying_closed:
                return
            first = not self._closed
            self._closed = True

        if first:
            self._interrupt_read()

        with self._state:
            while self._readers:
                self._state.wait()
            if self._underlying_closed:
                return
            self._underlying_closed = True
            self._state.notify_all()

        with suppress(OSError):
            self._response.close()

    def _begin_read(self) -> Any | None:
        with self._state:
            if self._closed:
                return None
            self._readers += 1
            return self._response

    def _end_read(self) -> None:
        with self._state:
            self._readers -= 1
            self._state.notify_all()

    def _interrupt_read(self) -> None:
        """Wake a blocked read without mutating the response's read state."""

        abort = getattr(self._response, "abort", None)
        if callable(abort):
            with suppress(OSError):
                abort()
            return

        shutdown = getattr(self._response, "shutdown", None)
        if callable(shutdown):
            with suppress(OSError, TypeError):
                shutdown(socket.SHUT_RDWR)
            return

        file_object = getattr(self._response, "fp", None)
        raw = getattr(file_object, "raw", None)
        candidates = (file_object, raw)
        for candidate in candidates:
            stream_socket = getattr(candidate, "_sock", None)
            if stream_socket is None:
                continue
            with suppress(OSError):
                stream_socket.shutdown(socket.SHUT_RDWR)
            return

    def __enter__(self) -> LoopbackResponse:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()


class ILoopbackTransport(Protocol):
    """The inward-facing HTTP seam used by every OpenCode local control caller."""

    def request(self, request: LoopbackRequest, *, timeout: float) -> AbstractContextManager[LoopbackResponse]:
        """Open a validated loopback request and yield its response."""

        ...


def _is_loopback_host(host: str) -> bool:
    if host.lower() == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def _validate_loopback_url(url: str) -> None:
    try:
        parsed = urlsplit(url)
        host = parsed.hostname
        port = parsed.port
    except ValueError as exc:
        raise LoopbackTransportError("the OpenCode control URL is malformed") from exc
    if (
        parsed.scheme != "http"
        or host is None
        or not _is_loopback_host(host)
        or port is None
        or parsed.username is not None
        or parsed.password is not None
        or parsed.fragment
    ):
        raise LoopbackTransportError("OpenCode control traffic must stay on an http loopback URL")


def _safe_headers(headers: Mapping[str, str]) -> dict[str, str]:
    """Keep explicit local headers while dropping credentials and connection controls."""

    safe: dict[str, str] = {}
    for name, value in headers.items():
        if not isinstance(name, str) or not isinstance(value, str):
            raise LoopbackTransportError("loopback request headers must be strings")
        normalized = name.lower()
        if normalized in _SENSITIVE_HEADERS or normalized in _HOP_BY_HOP_HEADERS:
            continue
        if normalized.startswith("proxy-") or normalized.startswith("x-forwarded-"):
            continue
        safe[name] = value
    return safe


class _LoopbackRedirectHandler(HTTPRedirectHandler):
    """Follow only loopback redirects and retain the original method and body."""

    def redirect_request(
        self,
        req: Request,
        fp: Any,
        code: int,
        msg: str,
        headers: Any,
        newurl: str,
    ) -> Request | None:
        del fp, code, msg, headers
        _validate_loopback_url(newurl)
        original_headers = {**req.headers, **req.unredirected_hdrs}
        return Request(
            newurl,
            data=req.data,
            headers=_safe_headers(original_headers),
            origin_req_host=req.origin_req_host,
            unverifiable=True,
            method=req.get_method(),
        )


class UrllibLoopbackTransport:
    """Direct, no-proxy urllib transport constrained to loopback HTTP."""

    @contextmanager
    def request(self, request: LoopbackRequest, *, timeout: float) -> Iterator[LoopbackResponse]:
        _validate_loopback_url(request.url)
        if (
            not isinstance(request.method, str)
            or not request.method
            or any(character.isspace() for character in request.method)
        ):
            raise LoopbackTransportError("the loopback request method is invalid")
        if timeout <= 0:
            raise LoopbackTransportError("the loopback request timeout must be positive")
        urllib_request = Request(
            request.url,
            data=request.body,
            headers=_safe_headers(request.headers),
            method=request.method,
        )
        try:
            # A per-request opener is both thread-safe and necessary when an idle SSE open is
            # waiting for the local action whose request will produce its first event.
            opener = build_opener(ProxyHandler({}), _LoopbackRedirectHandler())
            raw_response = opener.open(urllib_request, timeout=timeout)
        except HTTPError as error:
            # HTTP errors are still local responses. Callers need their status and
            # body to distinguish an upstream denial from a transport failure.
            raw_response = error
        except LoopbackTransportError:
            raise
        except (URLError, TimeoutError, OSError) as exc:
            raise LoopbackTransportError("the OpenCode loopback request failed") from exc

        response = LoopbackResponse(raw_response)
        try:
            yield response
        finally:
            with suppress(OSError):
                response.close()


def _conforms_loopback_transport(x: UrllibLoopbackTransport) -> ILoopbackTransport:
    return x


__all__ = [
    "LOCAL_SERVER_HOST",
    "LOCAL_SERVER_POLL_SECONDS",
    "LOCAL_SERVER_START_TIMEOUT_SECONDS",
    "ILoopbackTransport",
    "LoopbackRequest",
    "LoopbackResponse",
    "LoopbackTransportError",
    "UrllibLoopbackTransport",
    "local_server_argv",
    "wait_for_local_server",
]

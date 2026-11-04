"""The HTTP seam between the OpenCode 2 client and the service, held to loopback so the registered password never
leaves the machine."""

from __future__ import annotations

import ipaddress
from collections.abc import Generator, Iterator, Mapping
from contextlib import AbstractContextManager, contextmanager
from dataclasses import dataclass, field
from typing import Protocol
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener

from blizzard.foundation.roles import domain_model


class ServiceUnreachableError(ConnectionError):
    """No service answered at the registered address."""


class NonLoopbackServiceError(ValueError):
    """The registered address is not on loopback, so no credential is sent to it."""


@domain_model
@dataclass(frozen=True)
class ServiceRequest:
    method: str
    url: str
    headers: Mapping[str, str] = field(default_factory=dict[str, str], repr=False)
    body: bytes | None = None
    timeout_seconds: float = 30.0


@domain_model
@dataclass(frozen=True)
class ServiceResponse:
    status: int
    body: bytes


class IServiceTransport(Protocol):
    def send(self, request: ServiceRequest) -> ServiceResponse:
        """One request and its whole response; any HTTP status is a response, a refused connection raises
        :class:`ServiceUnreachableError`."""
        ...

    def stream(self, request: ServiceRequest) -> AbstractContextManager[Iterator[bytes]]:
        """The response body's lines while the connection stays open; the iterator ends when the service closes it."""
        ...


def require_loopback(url: str) -> None:
    host = urlsplit(url).hostname or ""
    if host == "localhost":
        return
    try:
        if ipaddress.ip_address(host).is_loopback:
            return
    except ValueError:
        pass
    raise NonLoopbackServiceError(f"the OpenCode service is registered at a non-loopback host: {host!r}")


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *args: object, **kwargs: object) -> None:
        return None


class UrllibServiceTransport:
    """Loopback-only urllib transport that consults no proxy and follows no redirect."""

    def __init__(self) -> None:
        self._opener = build_opener(ProxyHandler({}), _NoRedirect())

    def _request(self, request: ServiceRequest) -> Request:
        require_loopback(request.url)
        return Request(request.url, data=request.body, headers=dict(request.headers), method=request.method)

    def send(self, request: ServiceRequest) -> ServiceResponse:
        try:
            with self._opener.open(self._request(request), timeout=request.timeout_seconds) as response:
                return ServiceResponse(status=response.status, body=response.read())
        except HTTPError as exc:
            return ServiceResponse(status=exc.code, body=exc.read())
        except (URLError, ConnectionError, TimeoutError) as exc:
            raise ServiceUnreachableError(f"the OpenCode service did not answer: {exc}") from exc

    @contextmanager
    def stream(self, request: ServiceRequest) -> Generator[Iterator[bytes]]:
        try:
            response = self._opener.open(self._request(request), timeout=request.timeout_seconds)
        except HTTPError as exc:
            raise ServiceUnreachableError(f"the OpenCode event stream answered {exc.code}") from exc
        except (URLError, ConnectionError, TimeoutError) as exc:
            raise ServiceUnreachableError(f"the OpenCode service did not answer: {exc}") from exc
        with response:
            yield iter(response)


__all__ = [
    "IServiceTransport",
    "NonLoopbackServiceError",
    "ServiceRequest",
    "ServiceResponse",
    "ServiceUnreachableError",
    "UrllibServiceTransport",
    "require_loopback",
]

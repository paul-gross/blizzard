"""How a CLI test hands its commands their HTTP: collaborators bound over a canned transport
(:func:`bind_transport`) or per-verb callables (:func:`bind_stubs`), invoked through the returned :class:`Bound`."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any

import httpx2
from click.testing import CliRunner

from blizzard.cli.collaborators import CliCollaborators
from blizzard.foundation.clock import SystemClock
from blizzard.foundation.span_clock import Clock


class StubClient(httpx2.Client):
    """A client whose verbs are the callables a test supplies, each answering as ``httpx2.<verb>(url, **kwargs)``.

    ``request`` and ``stream`` take the method first; without a ``request`` callable, a request routes to its method's own."""

    def __init__(
        self,
        calls: dict[str, Callable[..., Any]],
        *,
        timeout: float | None = None,
        headers: Mapping[str, str] | None = None,
    ) -> None:
        super().__init__()
        self._calls = calls
        self._ambient: dict[str, Any] = {}
        if timeout is not None:
            self._ambient["timeout"] = timeout
        if headers:
            self._ambient["headers"] = dict(headers)
        self.closed = False

    def _verb(self, name: str, url: str, kwargs: dict[str, Any]) -> Any:
        call = self._calls.get(name)
        if call is None:
            raise AssertionError(f"unexpected {name.upper()} {url}")
        return call(url, **self._merged(kwargs))

    def _merged(self, kwargs: dict[str, Any]) -> dict[str, Any]:
        merged = {**self._ambient, **kwargs}
        if "headers" in self._ambient and "headers" in kwargs:
            merged["headers"] = {**self._ambient["headers"], **kwargs["headers"]}
        return merged

    def get(self, url: httpx2.URL | str, **kwargs: Any) -> Any:
        return self._verb("get", str(url), kwargs)

    def post(self, url: httpx2.URL | str, **kwargs: Any) -> Any:
        return self._verb("post", str(url), kwargs)

    def patch(self, url: httpx2.URL | str, **kwargs: Any) -> Any:
        return self._verb("patch", str(url), kwargs)

    def put(self, url: httpx2.URL | str, **kwargs: Any) -> Any:
        return self._verb("put", str(url), kwargs)

    def delete(self, url: httpx2.URL | str, **kwargs: Any) -> Any:
        return self._verb("delete", str(url), kwargs)

    def request(self, method: str, url: httpx2.URL | str, **kwargs: Any) -> Any:
        if "request" in self._calls:
            return self._calls["request"](method.lower(), str(url), **self._merged(kwargs))
        return self._verb(method.lower(), str(url), kwargs)

    def stream(self, method: str, url: httpx2.URL | str, **kwargs: Any) -> Any:
        call = self._calls.get("stream")
        if call is None:
            raise AssertionError(f"unexpected streamed {method} {url}")
        return call(method.lower(), str(url), **self._merged(kwargs))

    def close(self) -> None:
        self.closed = True

    def __enter__(self) -> StubClient:
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()


class _StubFactory:
    """Builds :class:`StubClient` instances over the callables a test supplies."""

    def __init__(self, calls: dict[str, Callable[..., Any]]) -> None:
        self.calls = calls
        self.built: list[StubClient] = []

    def __call__(
        self,
        *,
        base_url: str = "",
        timeout: float | None = None,
        headers: Mapping[str, str] | None = None,
        uds: str | None = None,
    ) -> StubClient:
        client = StubClient(self.calls, timeout=timeout, headers=headers)
        self.built.append(client)
        return client


class _BoundRunner(CliRunner):
    """A ``CliRunner`` whose every invocation hands the root group the bound collaborators."""

    def __init__(self, collaborators: CliCollaborators) -> None:
        super().__init__()
        self._collaborators = collaborators

    def invoke(self, *args: Any, **kwargs: Any) -> Any:
        kwargs.setdefault("obj", self._collaborators)
        return super().invoke(*args, **kwargs)


@dataclass(frozen=True)
class Bound:
    """The collaborators a test bound, the runner that hands them down, and the clients built from them."""

    collaborators: CliCollaborators
    runner: CliRunner
    built: list[Any]


def bind_stubs(
    *,
    get: Callable[..., Any] | None = None,
    post: Callable[..., Any] | None = None,
    patch: Callable[..., Any] | None = None,
    put: Callable[..., Any] | None = None,
    delete: Callable[..., Any] | None = None,
    request: Callable[..., Any] | None = None,
    stream: Callable[..., Any] | None = None,
) -> Bound:
    """Answer commands' requests with the verb callables given; any other verb fails the test."""
    given = {
        "get": get,
        "post": post,
        "patch": patch,
        "put": put,
        "delete": delete,
        "request": request,
        "stream": stream,
    }
    factory = _StubFactory({name: call for name, call in given.items() if call is not None})
    collaborators = CliCollaborators(client_factory=factory, clock=Clock(), wall_clock=SystemClock())
    return Bound(collaborators, _BoundRunner(collaborators), factory.built)


def bind_transport(handler: Callable[[httpx2.Request], httpx2.Response], *, clock: Clock | None = None) -> Bound:
    """Answer worker commands' requests through an ``httpx2.MockTransport``."""
    built: list[Any] = []

    def factory(
        *,
        base_url: str = "",
        timeout: float | None = None,
        headers: Mapping[str, str] | None = None,
        uds: str | None = None,
    ) -> httpx2.Client:
        client = httpx2.Client(transport=httpx2.MockTransport(handler), base_url=base_url, headers=headers)
        if timeout is not None:
            client.timeout = httpx2.Timeout(timeout)
        built.append(client)
        return client

    collaborators = CliCollaborators(client_factory=factory, clock=clock or Clock(), wall_clock=SystemClock())
    return Bound(collaborators, _BoundRunner(collaborators), built)


#: In-process daemon reads get a longer budget under loaded xdist; these tests do not assert latency.
_IN_PROCESS_DAEMON_TIMEOUT = 60.0


def bind_local_only(*, allow_base_urls: tuple[str, ...] = ()) -> Bound:
    """Real clients, but only to the runner's own local API or a ``base_url`` in ``allow_base_urls``; any other fails."""
    real = CliCollaborators.production().client_factory

    def factory(
        *,
        base_url: str = "",
        timeout: float | None = None,
        headers: Mapping[str, str] | None = None,
        uds: str | None = None,
    ) -> httpx2.Client:
        if uds is None and base_url not in allow_base_urls:
            raise AssertionError(
                f"a local verb contacted {base_url or 'a remote host'}; it must only reach the local API"
            )
        local_timeout = _IN_PROCESS_DAEMON_TIMEOUT if uds is not None else timeout
        return real(base_url=base_url, timeout=local_timeout, headers=headers, uds=uds)

    collaborators = CliCollaborators(client_factory=factory, clock=Clock(), wall_clock=SystemClock())
    return Bound(collaborators, _BoundRunner(collaborators), [])

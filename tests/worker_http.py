"""How a worker-CLI test hands its commands their HTTP: collaborators bound over a canned transport
(:func:`bind_transport`) or ``get``/``post`` callables (:func:`bind_stubs`), and invoked through the
returned :class:`Bound`'s runner."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

import httpx
from click.testing import CliRunner

from blizzard.cli.collaborators import CliCollaborators
from blizzard.foundation.span_clock import Clock


class StubClient:
    """A client whose ``get`` and ``post`` are the callables a test supplies."""

    def __init__(self, get: Callable[..., Any] | None, post: Callable[..., Any] | None) -> None:
        self._get = get
        self._post = post
        self.closed = False

    def get(self, url: str, **kwargs: Any) -> Any:
        if self._get is None:
            raise AssertionError(f"unexpected GET {url}")
        return self._get(url, **kwargs)

    def post(self, url: str, **kwargs: Any) -> Any:
        if self._post is None:
            raise AssertionError(f"unexpected POST {url}")
        return self._post(url, **kwargs)

    def close(self) -> None:
        self.closed = True


class _StubFactory:
    """Builds :class:`StubClient` instances over the ``get`` and ``post`` a test supplies."""

    def __init__(self, get: Callable[..., Any] | None, post: Callable[..., Any] | None) -> None:
        self.get = get
        self.post = post
        self.built: list[StubClient] = []

    def __call__(self) -> StubClient:
        client = StubClient(self.get, self.post)
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


def bind_stubs(*, get: Callable[..., Any] | None = None, post: Callable[..., Any] | None = None) -> Bound:
    """Answer worker commands' requests with ``get``/``post``."""
    factory = _StubFactory(get, post)
    collaborators = CliCollaborators(client_factory=factory, clock=Clock())  # type: ignore[arg-type]
    return Bound(collaborators, _BoundRunner(collaborators), factory.built)


def bind_transport(handler: Callable[[httpx.Request], httpx.Response], *, clock: Clock | None = None) -> Bound:
    """Answer worker commands' requests through an ``httpx.MockTransport``."""
    built: list[Any] = []

    def factory() -> httpx.Client:
        client = httpx.Client(transport=httpx.MockTransport(handler))
        built.append(client)
        return client

    collaborators = CliCollaborators(client_factory=factory, clock=clock or Clock())
    return Bound(collaborators, _BoundRunner(collaborators), built)

"""The shared seam through which a worker-CLI test replaces the HTTP its commands make.

A worker command makes every request, and posts its span, through the one client its
``WorkerSession`` builds. A test binds that client here, instead of stubbing ``httpx`` module
functions: either to a canned transport (:func:`bind_transport`) or, for a test that answers with
its own response objects, to ``get``/``post`` callables (:func:`bind_stubs`)."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import httpx
import pytest

from blizzard.runner.cli import worker_call


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
    """Builds :class:`StubClient` instances; a test binds ``get`` and ``post`` in separate calls."""

    def __init__(self) -> None:
        self.get: Callable[..., Any] | None = None
        self.post: Callable[..., Any] | None = None
        self.built: list[StubClient] = []

    def __call__(self) -> StubClient:
        client = StubClient(self.get, self.post)
        self.built.append(client)
        return client


def bind_stubs(
    monkeypatch: pytest.MonkeyPatch,
    *,
    get: Callable[..., Any] | None = None,
    post: Callable[..., Any] | None = None,
) -> list[StubClient]:
    """Answer worker commands' requests with ``get``/``post``; returns the clients built."""
    factory = worker_call.client_factory
    if not isinstance(factory, _StubFactory):
        factory = _StubFactory()
        monkeypatch.setattr(worker_call, "client_factory", factory)
    factory.get = get or factory.get
    factory.post = post or factory.post
    return factory.built


def bind_transport(
    monkeypatch: pytest.MonkeyPatch, handler: Callable[[httpx.Request], httpx.Response]
) -> list[httpx.Client]:
    """Answer the next worker command's requests through an ``httpx.MockTransport``."""
    built: list[httpx.Client] = []

    def factory() -> httpx.Client:
        client = httpx.Client(transport=httpx.MockTransport(handler))
        built.append(client)
        return client

    monkeypatch.setattr(worker_call, "client_factory", factory)
    return built

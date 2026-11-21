"""The guarded resolver: concurrent resolution shares one instance without waiting on a parent, closure refuses new
resolutions, and injection's positional ``component`` reaches the raw container."""

import threading
from collections.abc import Awaitable, Callable, Iterator
from typing import Annotated

import pytest
from dishka import BaseScope, FromComponent, Provider, make_container, new_scope
from dishka.integrations.fastapi import inject_sync
from fastapi import FastAPI, Request, Response
from fastapi.testclient import TestClient

from blizzard.foundation.registry.interface import Lifetime, ScopeClosed
from blizzard.foundation.registry.internal.resolver import ContainerOwner


class _Scopes(BaseScope):
    PROCESS = new_scope("process")
    TENANT = new_scope("tenant")


_LIFETIMES = (Lifetime.PROCESS, Lifetime.TENANT)


class _ParentThing:
    pass


class _ChildThing:
    pass


def _owner(provider: Provider) -> ContainerOwner:
    container = make_container(provider, scopes=_Scopes, lock_factory=threading.Lock)
    return ContainerOwner(container, lifetimes=_LIFETIMES, owner="hub")


def _provider() -> Provider:
    provider = Provider()
    provider.provide(_ParentThing, scope=_Scopes.PROCESS)
    provider.provide(_ChildThing, scope=_Scopes.TENANT)
    return provider


def test_many_threads_resolve_one_child_instance_without_waiting_on_the_parent() -> None:
    gate = threading.Event()
    entered = threading.Event()
    provider = Provider()

    def slow_parent() -> _ParentThing:
        entered.set()
        gate.wait(timeout=10)
        return _ParentThing()

    provider.provide(slow_parent, scope=_Scopes.PROCESS)
    provider.provide(_ChildThing, scope=_Scopes.TENANT)
    process = _owner(provider)
    child = process.open_child(lifetime=Lifetime.TENANT, owner="t1", values={})

    blocked = threading.Thread(target=lambda: process.resolver.get(_ParentThing))
    blocked.start()
    assert entered.wait(timeout=10)

    results: list[_ChildThing] = []
    lock = threading.Lock()

    def resolve() -> None:
        found = child.resolver.get(_ChildThing)
        with lock:
            results.append(found)

    workers = [threading.Thread(target=resolve) for _ in range(32)]
    for worker in workers:
        worker.start()
    for worker in workers:
        worker.join(timeout=10)
    assert not any(worker.is_alive() for worker in workers)
    assert len({id(found) for found in results}) == 1
    assert len(results) == 32

    gate.set()
    blocked.join(timeout=10)
    child.close()
    process.close()


def test_a_resolution_racing_a_close_is_refused() -> None:
    gate = threading.Event()
    entered = threading.Event()
    provider = Provider()

    def slow_child() -> Iterator[_ChildThing]:
        entered.set()
        gate.wait(timeout=10)
        yield _ChildThing()

    provider.provide(slow_child, scope=_Scopes.TENANT)
    process = _owner(provider)
    child = process.open_child(lifetime=Lifetime.TENANT, owner="t1", values={})

    in_flight = threading.Thread(target=lambda: child.resolver.get(_ChildThing))
    in_flight.start()
    assert entered.wait(timeout=10)

    closer = threading.Thread(target=child.close)
    closer.start()
    deadline = threading.Event()
    while not child._guard.closing:  # pyright: ignore[reportPrivateUsage]  # the test waits for the close to begin
        deadline.wait(0.01)

    with pytest.raises(ScopeClosed) as refused:
        child.resolver.get(_ChildThing)
    assert refused.value.lifetime is Lifetime.TENANT
    assert refused.value.owner == "t1"

    gate.set()
    in_flight.join(timeout=10)
    closer.join(timeout=10)
    assert not closer.is_alive()
    process.close()


def test_a_lookup_through_a_child_after_its_parent_closes_names_the_parent() -> None:
    process = _owner(_provider())
    child = process.open_child(lifetime=Lifetime.TENANT, owner="t1", values={})
    process.close()
    with pytest.raises(ScopeClosed) as refused:
        child.resolver.get(_ChildThing)
    assert refused.value.lifetime is Lifetime.PROCESS
    assert refused.value.owner == "hub"
    child.close()


def test_a_closed_owner_refuses_a_new_child_and_close_is_idempotent() -> None:
    finalized: list[str] = []
    provider = Provider()

    def thing() -> Iterator[_ParentThing]:
        yield _ParentThing()
        finalized.append("closed")

    provider.provide(thing, scope=_Scopes.PROCESS)
    process = _owner(provider)
    process.resolver.get(_ParentThing)
    process.close()
    process.close()
    assert finalized == ["closed"]
    with pytest.raises(ScopeClosed):
        process.open_child(lifetime=Lifetime.TENANT, owner="t1", values={})


def test_a_child_opens_only_the_next_lifetime() -> None:
    process = _owner(_provider())
    with pytest.raises(ValueError, match="next lifetime"):
        process.open_child(lifetime=Lifetime.REQUEST, owner="r", values={})
    process.close()


def test_closing_a_child_leaves_its_parent_open() -> None:
    process = _owner(_provider())
    child = process.open_child(lifetime=Lifetime.TENANT, owner="t1", values={})
    child.close()
    assert isinstance(process.resolver.get(_ParentThing), _ParentThing)
    process.close()


class _Named:
    pass


def test_inject_sync_resolves_a_component_keyed_dependency_positionally() -> None:
    provider = Provider(component="c")
    provider.provide(_Named, scope=_Scopes.PROCESS)
    process = _owner(provider)

    app = FastAPI()
    seen: list[_Named] = []

    @app.middleware("http")
    async def attach(request: Request, call_next: Callable[[Request], Awaitable[Response]]) -> Response:
        request.state.dishka_container = process.resolver
        return await call_next(request)

    @app.get("/")
    @inject_sync
    def handler(request: Request, named: Annotated[_Named, FromComponent("c")]) -> dict[str, bool]:
        seen.append(named)
        return {"ok": True}

    assert TestClient(app).get("/").json() == {"ok": True}
    assert seen[0] is process.resolver.get(_Named, "c")
    assert process.resolver.get(_Named, component="c") is seen[0]
    process.close()

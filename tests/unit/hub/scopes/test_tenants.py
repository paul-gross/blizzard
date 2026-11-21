"""``TenantContainers`` over the real hub wiring and a test-owned graph: one scope per tenant, ordered teardown (unit tier)."""

from __future__ import annotations

import asyncio
import threading
from collections.abc import Callable, Iterator
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Any

import pytest
import structlog
from sqlalchemy import Engine, create_engine

from blizzard.foundation.registry.interface import Lifetime, ScopeClosed, port
from blizzard.foundation.registry.internal.recording import RecordingRegistry
from blizzard.foundation.registry.internal.translation import HUB_LIFETIMES, build_container
from blizzard.foundation.registry.scopes import OwnedChild, ScopeValues, open_child, supply
from blizzard.hub.delivery.action_loop import ActionLoop
from blizzard.hub.events import wiring as events_wiring
from blizzard.hub.events.broker import EventBroker
from blizzard.hub.scopes import wiring as scopes_wiring
from blizzard.hub.scopes.tenants import TenantContainers
from blizzard.hub.settings.graph import HubSettingsGraph
from blizzard.hub.store.errors import HubStoreConnections, HubStoreErrorFactory
from blizzard.hub.tenancy import StoreScope, TenantClosed, TenantId, UnknownTenant

T1, T2, T3 = TenantId("ten_1"), TenantId("ten_2"), TenantId("ten_3")


class TenantStore:
    """A per-tenant store built from the tenant's ``StoreScope``."""

    def __init__(self, scope: StoreScope) -> None:
        self.scope = scope


class Shared:
    """A tenant service over the store; its constructions are counted."""

    built: list[TenantId]

    def __init__(self, store: TenantStore) -> None:
        self.store = store
        Shared.built.append(store.scope.tenant)

    def operate(self, started: threading.Event, release: threading.Event) -> None:
        started.set()
        release.wait(5)


class Journal:
    """A borrowed process input that records what happens around it."""

    def __init__(self) -> None:
        self.entries: list[str] = []
        self.lock = threading.Lock()

    def note(self, entry: str) -> None:
        with self.lock:
            self.entries.append(entry)


class Bomb:
    """What fails to initialize while ``armed``."""

    armed = True


def _bomb(scope: StoreScope) -> Bomb:
    if Bomb.armed:
        raise RuntimeError(f"initialization of {scope.tenant} failed")
    return Bomb()


@dataclass
class Gate:
    """Holds a resource's construction until released."""

    started: threading.Event = field(default_factory=threading.Event)
    release: threading.Event = field(default_factory=threading.Event)


class Slow:
    pass


def _open_slow(scope: StoreScope, journal: Journal, gate: Gate) -> Iterator[Slow]:
    journal.note(f"slow:open:{scope.tenant}")
    gate.started.set()
    gate.release.wait(5)
    try:
        yield Slow()
    finally:
        journal.note(f"slow:close:{scope.tenant}")


class Marked:
    pass


def _open_marked(scope: StoreScope, journal: Journal) -> Iterator[Marked]:
    journal.note(f"marked:open:{scope.tenant}")
    try:
        yield Marked()
    finally:
        journal.note(f"marked:close:{scope.tenant}")


class Stubborn:
    pass


def open_stubborn(scope: StoreScope) -> Iterator[Stubborn]:
    yield Stubborn()
    raise OSError("close refused")


class Failing:
    pass


def open_failing(scope: StoreScope, journal: Journal) -> Iterator[Failing]:
    yield Failing()
    journal.note(f"failing:close:{scope.tenant}")
    raise OSError(f"{scope.tenant} would not close")


def _with_stubborn(registry: RecordingRegistry) -> None:
    registry.resource(open_stubborn, lifetime=Lifetime.TENANT)


def _with_failing(registry: RecordingRegistry) -> None:
    registry.resource(open_failing, lifetime=Lifetime.TENANT)


class Principal:
    pass


class RequestHandler:
    def __init__(self, shared: Shared, principal: Principal) -> None:
        self.shared = shared
        self.principal = principal


@dataclass
class Rig:
    registry: RecordingRegistry
    journal: Journal
    gate: Gate
    engine: Engine
    process: OwnedChild
    containers: TenantContainers
    confirm: Callable[[TenantId], None]


def _confirm_all(tenant: TenantId) -> None:
    return None


def _rig(
    *,
    extra: Callable[[RecordingRegistry], None] = lambda registry: None,
    values: Callable[[Journal], tuple[Any, ...]] = lambda journal: (),
    confirm: Callable[[TenantId], None] = _confirm_all,
    open_tenants: Callable[[], list[TenantId]] = lambda: [T2, T1],
    eager: tuple[type[Any], ...] = (),
) -> Rig:
    Shared.built = []
    Bomb.armed = True
    registry = RecordingRegistry()
    scopes_wiring.register(registry)
    events_wiring.register(registry)
    registry.context(port(Journal), lifetime=Lifetime.PROCESS)
    registry.context(port(Gate), lifetime=Lifetime.PROCESS)
    registry.service(TenantStore, lifetime=Lifetime.TENANT)
    registry.service(Shared, lifetime=Lifetime.TENANT)
    registry.resource(_open_marked, lifetime=Lifetime.TENANT)
    registry.resource(_open_slow, lifetime=Lifetime.TENANT)
    registry.factory(_bomb, lifetime=Lifetime.TENANT)
    registry.context(port(Principal), lifetime=Lifetime.REQUEST)
    registry.service(RequestHandler, lifetime=Lifetime.REQUEST)
    extra(registry)
    journal, gate = Journal(), Gate()
    engine = create_engine("sqlite://")
    connections = HubStoreConnections(engine, HubStoreErrorFactory(structlog.get_logger()))
    settings = HubSettingsGraph.__new__(HubSettingsGraph)
    process = build_container(
        registry,
        HUB_LIFETIMES,
        owner="hub",
        values=ScopeValues(
            (
                supply(port(Engine), engine),
                supply(port(HubStoreConnections), connections),
                supply(port(HubSettingsGraph), settings),
                supply(port(Journal), journal),
                supply(port(Gate), gate),
                *values(journal),
            )
        ),
    )
    containers = TenantContainers(process, confirm_open=confirm, open_tenants=open_tenants, eager=eager)
    return Rig(registry, journal, gate, engine, process, containers, confirm)


def _race[T](count: int, work: Callable[[], T]) -> list[T]:
    barrier = threading.Barrier(count)

    def go() -> T:
        barrier.wait()
        return work()

    with ThreadPoolExecutor(count) as pool:
        return [future.result() for future in [pool.submit(go) for _ in range(count)]]


def test_racing_opens_yield_one_scope_and_one_service_per_tenant() -> None:
    rig = _rig()
    scope = StoreScope(T1)
    shared = _race(32, lambda: rig.containers.resolve(scope, Shared))
    opened = _race(32, lambda: rig.containers.open(scope).resolver.get(Shared))
    assert {id(each) for each in shared + opened} == {id(shared[0])}
    assert Shared.built == [T1]
    rig.containers.close()
    rig.process.close()


def test_two_tenants_get_different_stores_and_brokers() -> None:
    rig = _rig()
    first, second = StoreScope(T1), StoreScope(T2)
    assert rig.containers.resolve(first, TenantStore) is not rig.containers.resolve(second, TenantStore)
    assert rig.containers.resolve(first, TenantStore).scope == first
    assert rig.containers.resolve(first, EventBroker) is not rig.containers.resolve(second, EventBroker)
    assert rig.containers.resolve(first, EventBroker) is rig.containers.resolve(first, EventBroker)
    rig.containers.close()
    rig.process.close()


def test_a_lookup_after_closure_raises_tenant_closed_and_every_open_yields_only_open_tenants() -> None:
    def confirm(tenant: TenantId) -> None:
        if tenant == T3:
            raise UnknownTenant(tenant)

    rig = _rig(confirm=confirm, open_tenants=lambda: [T3, T2, T1])
    assert [tenant for tenant, _ in rig.containers.every_open(TenantStore)] == [T1, T2]
    rig.containers.close()
    with pytest.raises(TenantClosed) as closed:
        rig.containers.resolve(StoreScope(T1), TenantStore)
    assert closed.value.tenant == T1
    with pytest.raises(TenantClosed):
        rig.containers.open(StoreScope(T1))
    with pytest.raises(TenantClosed):
        rig.containers.request(StoreScope(T1), ScopeValues())
    assert list(rig.containers.every_open(TenantStore)) == []
    rig.process.close()


def test_a_tenant_closed_in_the_store_is_refused_and_skipped_by_every_open() -> None:
    closed_tenants: set[TenantId] = set()

    def confirm(tenant: TenantId) -> None:
        if tenant in closed_tenants:
            raise TenantClosed(tenant)

    rig = _rig(confirm=confirm)
    rig.containers.resolve(StoreScope(T1), TenantStore)
    closed_tenants.add(T1)
    rig.containers.evict(T1)
    with pytest.raises(TenantClosed):
        rig.containers.resolve(StoreScope(T1), TenantStore)
    assert [tenant for tenant, _ in rig.containers.every_open(TenantStore)] == [T2]
    rig.containers.close()
    rig.process.close()


def test_eviction_closes_only_the_selected_tenant() -> None:
    rig = _rig()
    for tenant in (T1, T2):
        rig.containers.resolve(StoreScope(tenant), Marked)
    rig.containers.evict(T1)
    assert rig.journal.entries == [f"marked:open:{T1}", f"marked:open:{T2}", f"marked:close:{T1}"]
    assert rig.containers.resolve(StoreScope(T2), Marked) is not None
    rig.containers.close()
    rig.process.close()


def test_a_failed_initialization_publishes_nothing_and_the_next_open_builds_afresh() -> None:
    rig = _rig(eager=(Marked, Bomb))
    scope = StoreScope(T1)
    with pytest.raises(RuntimeError, match="initialization of ten_1 failed"):
        rig.containers.open(scope)
    assert rig.journal.entries == [f"marked:open:{T1}", f"marked:close:{T1}"]
    Bomb.armed = False
    rig.containers.open(scope)
    assert rig.journal.entries.count(f"marked:open:{T1}") == 2
    rig.containers.close()
    rig.process.close()


def test_a_cleanup_failure_during_a_failed_initialization_is_attached_to_the_original_error() -> None:
    rig = _rig(extra=_with_stubborn, eager=(Stubborn, Bomb))
    with pytest.raises(RuntimeError, match="initialization") as failure:
        rig.containers.open(StoreScope(T1))
    assert any("close refused" in note for note in failure.value.__notes__)
    rig.process.close()


def test_repeated_evict_and_close_are_harmless_and_close_leaves_borrowed_inputs_open() -> None:
    rig = _rig()
    rig.containers.resolve(StoreScope(T1), Marked)
    rig.containers.evict(T1)
    rig.containers.evict(T1)
    rig.containers.evict(T2)
    rig.containers.resolve(StoreScope(T2), Marked)
    rig.containers.close()
    rig.containers.close()
    assert rig.journal.entries.count(f"marked:close:{T2}") == 1
    assert rig.process.resolver.get(Journal) is rig.journal
    assert rig.process.resolver.get(Engine) is rig.engine
    rig.process.close()
    assert rig.journal.entries.count(f"marked:close:{T2}") == 1


def test_close_attempts_every_tenant_and_raises_the_failures_together() -> None:
    rig = _rig(extra=_with_failing)
    for tenant in (T1, T2):
        rig.containers.resolve(StoreScope(tenant), Failing)
    with pytest.raises(ExceptionGroup) as group:
        rig.containers.close()
    assert len(rig.journal.entries) == 2
    assert group.value.exceptions
    rig.process.close()


def test_a_close_racing_a_first_resolution_finalizes_a_completed_resource_once_and_refuses_later_lookups() -> None:
    rig = _rig()
    scope = StoreScope(T1)
    request = rig.containers.request(scope, ScopeValues((supply(port(Principal), Principal()),)))
    resolved: list[Slow] = []
    resolver = threading.Thread(target=lambda: resolved.append(rig.containers.resolve(scope, Slow)))
    resolver.start()
    assert rig.gate.started.wait(5)
    closer = threading.Thread(target=rig.containers.close)
    closer.start()
    closer.join(0.2)
    assert closer.is_alive()
    rig.gate.release.set()
    resolver.join(5)
    closer.join(5)
    assert not closer.is_alive()
    assert len(resolved) == 1
    assert rig.journal.entries == [f"slow:open:{T1}", f"slow:close:{T1}"]
    with pytest.raises(TenantClosed):
        rig.containers.resolve(scope, Slow)
    with pytest.raises(TenantClosed):
        request.resolver.get(RequestHandler)
    rig.process.close()


def test_close_does_not_wait_on_a_service_operation_running_after_resolution() -> None:
    rig = _rig()
    shared = rig.containers.resolve(StoreScope(T1), Shared)
    started, release = threading.Event(), threading.Event()
    operation = threading.Thread(target=shared.operate, args=(started, release))
    operation.start()
    assert started.wait(5)
    finished = threading.Thread(target=rig.containers.close)
    finished.start()
    finished.join(5)
    assert not finished.is_alive()
    release.set()
    operation.join(5)
    rig.process.close()


def test_a_request_scope_sees_the_tenants_service_and_a_principal_never_enters_the_tenant_scope() -> None:
    rig = _rig()
    scope = StoreScope(T1)
    request = rig.containers.request(scope, ScopeValues((supply(port(Principal), Principal()),)))
    handler = request.resolver.get(RequestHandler)
    assert handler.shared is rig.containers.resolve(scope, Shared)
    with pytest.raises(Exception, match="Principal"):
        open_child(
            rig.process,
            lifetime=Lifetime.TENANT,
            owner="x",
            values=ScopeValues((supply(port(Principal), Principal()),)),
        )
    request.close()
    rig.containers.close()
    rig.process.close()


def test_a_closed_process_scope_is_not_reported_as_a_closed_tenant() -> None:
    rig = _rig()
    rig.process.close()
    with pytest.raises(ScopeClosed) as closed:
        rig.containers.resolve(StoreScope(T1), Shared)
    assert closed.value.lifetime is Lifetime.PROCESS


class Started:
    """The action loop, started for the process and stopped when its scope closes."""

    def __init__(self, loop: ActionLoop) -> None:
        self.loop = loop


class ForgeClient:
    """Thread-confined: constructed on, and closed through, the action loop."""

    def __init__(self, thread: str) -> None:
        self.thread = thread


def test_shared_services_resolve_to_one_instance_from_every_caller_and_shutdown_runs_in_order() -> None:
    loop = ActionLoop()

    def run_loop(actions: ActionLoop, journal: Journal) -> Iterator[Started]:
        actions.start()
        try:
            yield Started(actions)
        finally:
            journal.note("loop:stop")
            actions.stop()

    def open_client(started: Started, scope: StoreScope, journal: Journal) -> Iterator[ForgeClient]:
        async def construct() -> ForgeClient:
            return ForgeClient(threading.current_thread().name)

        async def close() -> None:
            journal.note(f"client:close:{threading.current_thread().name}")

        client = started.loop.submit(construct()).result()
        try:
            yield client
        finally:
            started.loop.submit(close()).result()

    def extra(registry: RecordingRegistry) -> None:
        registry.context(port(ActionLoop), lifetime=Lifetime.PROCESS)
        registry.resource(run_loop, lifetime=Lifetime.PROCESS)
        registry.resource(open_client, lifetime=Lifetime.TENANT)

    rig = _rig(extra=extra, values=lambda journal: (supply(port(ActionLoop), loop),))
    rig.process.resolver.get(Started)
    scope = StoreScope(T1)

    from_threads = _race(8, lambda: rig.containers.resolve(scope, Shared))

    async def in_api_loop() -> Shared:
        return rig.containers.resolve(scope, Shared)

    from_api_loop = asyncio.run(in_api_loop())
    from_action_loop = loop.submit(in_api_loop()).result()
    assert {id(each) for each in [*from_threads, from_api_loop, from_action_loop]} == {id(from_threads[0])}
    client = rig.containers.resolve(scope, ForgeClient)
    assert client.thread == "hub-action-loop"

    rig.containers.close()
    rig.process.close()
    rig.journal.note("engine:dispose")
    rig.engine.dispose()
    assert rig.journal.entries[-3:] == ["client:close:hub-action-loop", "loop:stop", "engine:dispose"]

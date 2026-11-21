"""The translation builds strictly validated containers from recorded registrations, per process lifetime path."""

import tomllib
from collections.abc import Callable, Generator, Iterator
from pathlib import Path
from typing import Protocol

import pytest
from dishka import Container

from blizzard.foundation.registry.interface import Lifetime, Ref, ScopeClosed, ServiceAbsent, port
from blizzard.foundation.registry.internal.recording import RecordingRegistry
from blizzard.foundation.registry.internal.resolver import ContainerOwner, ScopeValuesRefused
from blizzard.foundation.registry.internal.translation import (
    COMMAND_LIFETIMES,
    HOST_LIFETIMES,
    HUB_LIFETIMES,
    GraphRefused,
    ProcessLifetimes,
    build_container,
)
from blizzard.foundation.registry.scopes import ScopeValues, open_child, supply

HUB = ScopeValues()


class IClock(Protocol):
    def now(self) -> int: ...


class WallClock(IClock, Protocol): ...


class ScheduleClock(IClock, Protocol): ...


class SystemClock:
    def now(self) -> int:
        return 1


class Engine:
    pass


class StoreScope:
    def __init__(self, name: str) -> None:
        self.name = name


class Principal:
    pass


class Store:
    def __init__(self, engine: Engine) -> None:
        self.engine = engine


class TenantService:
    def __init__(self, store: Store, scope: StoreScope) -> None:
        self.store = store
        self.scope = scope


class RequestHandler:
    def __init__(self, service: TenantService, principal: Principal) -> None:
        self.service = service
        self.principal = principal


class Needy:
    def __init__(self, missing: Principal) -> None:
        self.missing = missing


class Defaulted:
    def __init__(self, limit: int = 32) -> None:
        self.limit = limit


class CycleA:
    def __init__(self, other: "CycleB") -> None:
        self.other = other


class CycleB:
    def __init__(self, other: CycleA) -> None:
        self.other = other


class ProcessNeedsRequest:
    def __init__(self, principal: Principal) -> None:
        self.principal = principal


def _hub() -> tuple[RecordingRegistry, Ref[Store]]:
    registry = RecordingRegistry()
    registry.context(port(Engine), lifetime=Lifetime.PROCESS)
    store = registry.service(Store, lifetime=Lifetime.PROCESS)
    registry.context(port(StoreScope), lifetime=Lifetime.TENANT)
    registry.service(TenantService, lifetime=Lifetime.TENANT)
    registry.context(port(Principal), lifetime=Lifetime.REQUEST)
    registry.service(RequestHandler, lifetime=Lifetime.REQUEST)
    return registry, store


def _open_hub(registry: RecordingRegistry) -> ContainerOwner:
    return build_container(registry, HUB_LIFETIMES, owner="hub", values=ScopeValues((supply(port(Engine), Engine()),)))


def test_a_hub_graph_builds_and_resolves_through_its_lifetimes() -> None:
    registry, _ = _hub()
    process = _open_hub(registry)
    tenant = open_child(
        process,
        lifetime=Lifetime.TENANT,
        owner="t1",
        values=ScopeValues((supply(port(StoreScope), StoreScope("t1")),)),
    )
    request = open_child(
        tenant, lifetime=Lifetime.REQUEST, owner="r1", values=ScopeValues((supply(port(Principal), Principal()),))
    )
    handler = request.resolver.get(RequestHandler)
    assert handler.service is tenant.resolver.get(TenantService)
    assert handler.service.store is process.resolver.get(Store)
    request.close()
    tenant.close()
    process.close()


def test_a_host_graph_builds_through_runner_and_operation() -> None:
    registry = RecordingRegistry()
    registry.context(port(Engine), lifetime=Lifetime.PROCESS)
    registry.service(Store, lifetime=Lifetime.PROCESS)
    registry.context(port(StoreScope), lifetime=Lifetime.RUNNER)
    registry.service(TenantService, lifetime=Lifetime.RUNNER)
    registry.context(port(Principal), lifetime=Lifetime.OPERATION)
    registry.service(RequestHandler, lifetime=Lifetime.OPERATION)
    process = build_container(
        registry, HOST_LIFETIMES, owner="host", values=ScopeValues((supply(port(Engine), Engine()),))
    )
    runner = open_child(
        process,
        lifetime=Lifetime.RUNNER,
        owner="rn1",
        values=ScopeValues((supply(port(StoreScope), StoreScope("r")),)),
    )
    operation = open_child(
        runner,
        lifetime=Lifetime.OPERATION,
        owner="op",
        values=ScopeValues((supply(port(Principal), Principal()),)),
    )
    assert isinstance(operation.resolver.get(RequestHandler), RequestHandler)
    operation.close()
    runner.close()
    process.close()


def _refused(build: Callable[[RecordingRegistry], object], lifetimes: ProcessLifetimes = HUB_LIFETIMES) -> GraphRefused:
    registry = RecordingRegistry()
    build(registry)
    with pytest.raises(GraphRefused) as refused:
        build_container(registry, lifetimes, owner="x", values=HUB)
    return refused.value


def test_a_missing_dependency_is_refused() -> None:
    error = _refused(lambda registry: registry.service(Needy, lifetime=Lifetime.PROCESS))
    assert error.__cause__ is not None


def test_a_duplicate_owner_is_refused() -> None:
    def build(registry: RecordingRegistry) -> None:
        registry.service(Engine, lifetime=Lifetime.PROCESS)
        registry.service(Engine, lifetime=Lifetime.PROCESS)

    assert _refused(build).__cause__ is not None


def test_a_cycle_is_refused() -> None:
    def build(registry: RecordingRegistry) -> None:
        registry.service(CycleA, lifetime=Lifetime.PROCESS)
        registry.service(CycleB, lifetime=Lifetime.PROCESS)

    assert _refused(build).__cause__ is not None


def test_a_longer_lived_service_needing_a_shorter_lived_value_is_refused() -> None:
    def build(registry: RecordingRegistry) -> None:
        registry.context(port(Principal), lifetime=Lifetime.REQUEST)
        registry.service(ProcessNeedsRequest, lifetime=Lifetime.PROCESS)

    assert _refused(build).__cause__ is not None


def test_a_defaulted_constructor_is_refused_by_the_translation_even_with_the_type_registered() -> None:
    def build(registry: RecordingRegistry) -> None:
        registry.service(Defaulted, lifetime=Lifetime.PROCESS)

    error = _refused(build)
    assert error.__cause__ is None
    assert "default" in str(error)


def test_a_defaulted_constructor_is_refused_when_int_is_registered_too() -> None:
    def make_int() -> int:
        return 32

    def build(registry: RecordingRegistry) -> None:
        registry.factory(make_int, lifetime=Lifetime.PROCESS)
        registry.service(Defaulted, lifetime=Lifetime.PROCESS)

    assert "default" in str(_refused(build))


def test_a_factory_with_a_default_or_unannotated_parameter_is_refused() -> None:
    def defaulted(engine: Engine, limit: int = 3) -> Store:
        return Store(engine)

    assert "default" in str(_refused(lambda registry: registry.factory(defaulted, lifetime=Lifetime.PROCESS)))


def test_the_hub_refuses_runner_and_the_host_refuses_tenant_and_command() -> None:
    assert "runner" in str(_refused(lambda registry: registry.service(Engine, lifetime=Lifetime.RUNNER)))
    assert "tenant" in str(
        _refused(lambda registry: registry.service(Engine, lifetime=Lifetime.TENANT), HOST_LIFETIMES)
    )
    assert "command" in str(
        _refused(lambda registry: registry.service(Engine, lifetime=Lifetime.COMMAND), HOST_LIFETIMES)
    )
    assert "operation" in str(_refused(lambda registry: registry.service(Engine, lifetime=Lifetime.OPERATION)))


def test_a_command_graph_closes_its_resources_on_exit_and_on_failure() -> None:
    log: list[str] = []

    def open_engine() -> Iterator[Engine]:
        yield Engine()
        log.append("engine closed")

    for fail in (False, True):
        log.clear()
        registry = RecordingRegistry()
        registry.resource(open_engine, lifetime=Lifetime.COMMAND)
        registry.context(port(Principal), lifetime=Lifetime.COMMAND)
        borrowed = Principal()
        command = build_container(
            registry, COMMAND_LIFETIMES, owner="cmd", values=ScopeValues((supply(port(Principal), borrowed),))
        )
        command.resolver.get(Engine)
        command.resolver.get(Principal)
        command.close(RuntimeError("boom") if fail else None)
        assert log == ["engine closed"]


def test_a_resource_generator_sees_the_exception_the_scope_closes_with() -> None:
    seen: list[BaseException | None] = []

    def open_engine() -> Generator[Engine, BaseException | None, None]:
        seen.append((yield Engine()))

    registry = RecordingRegistry()
    registry.resource(open_engine, lifetime=Lifetime.COMMAND)
    command = build_container(registry, COMMAND_LIFETIMES, owner="cmd", values=HUB)
    command.resolver.get(Engine)
    failure = RuntimeError("body failed")
    command.close(failure)
    assert seen == [failure]


def test_two_roles_over_one_type_stay_distinct_and_aliases_share_one_instance_and_finalizer() -> None:
    finalized: list[str] = []

    def open_store() -> Iterator[Store]:
        yield Store(Engine())
        finalized.append("store")

    class ReadStore(Protocol):
        engine: Engine

    class WriteStore(Protocol):
        engine: Engine

    registry = RecordingRegistry()

    def wall_clock() -> WallClock:
        return SystemClock()

    def schedule_clock() -> ScheduleClock:
        return SystemClock()

    registry.factory(wall_clock, lifetime=Lifetime.PROCESS)
    registry.factory(schedule_clock, lifetime=Lifetime.PROCESS)
    store = registry.resource(open_store, lifetime=Lifetime.PROCESS)
    registry.alias(port(ReadStore), store)
    registry.alias(port(WriteStore), store)
    process = build_container(registry, HUB_LIFETIMES, owner="hub", values=HUB)
    resolver = process.resolver
    assert resolver.get(WallClock) is not resolver.get(ScheduleClock)
    assert resolver.get(ReadStore) is resolver.get(WriteStore) is resolver.get(Store)
    process.close()
    assert finalized == ["store"]


def test_absent_raises_its_label_and_the_graph_still_validates() -> None:
    registry = RecordingRegistry()
    registry.absent(port(Engine), lifetime=Lifetime.PROCESS, label="no engine here")
    process = build_container(registry, HUB_LIFETIMES, owner="hub", values=HUB)
    with pytest.raises(ServiceAbsent) as absent:
        process.resolver.get(Engine)
    assert absent.value.label == "no engine here"
    process.close()


def test_optional_resolves_to_the_target_instance_or_to_none() -> None:
    registry = RecordingRegistry()
    engine = registry.service(Engine, lifetime=Lifetime.PROCESS)
    registry.optional(port(Engine), engine, lifetime=Lifetime.PROCESS)
    registry.absent(port(Store), lifetime=Lifetime.PROCESS, label="no store")
    registry.optional(port(Store), None, lifetime=Lifetime.PROCESS)
    process = build_container(registry, HUB_LIFETIMES, owner="hub", values=HUB)
    assert process.resolver.get(Engine | None) is process.resolver.get(Engine)
    assert process.resolver.get(Store | None) is None
    process.close()


def test_a_resource_that_fails_part_way_unwinds_itself_and_completed_ones_close_at_scope_close() -> None:
    log: list[str] = []

    def open_engine() -> Iterator[Engine]:
        yield Engine()
        log.append("engine closed")

    def open_store(engine: Engine) -> Iterator[Store]:
        log.append("partial acquired")
        try:
            raise OSError("second step failed")
        finally:
            log.append("partial unwound")
        yield Store(engine)  # pragma: no cover

    registry = RecordingRegistry()
    registry.resource(open_engine, lifetime=Lifetime.PROCESS)
    registry.resource(open_store, lifetime=Lifetime.PROCESS)
    process = build_container(registry, HUB_LIFETIMES, owner="hub", values=HUB)
    with pytest.raises(OSError, match="second step"):
        process.resolver.get(Store)
    assert log == ["partial acquired", "partial unwound"]
    process.close()
    assert log[-1] == "engine closed"


def test_close_runs_every_finalizer_and_surfaces_an_exception_group_and_borrowed_values_stay_open() -> None:
    log: list[str] = []

    class Closable:
        closed = False

        def close(self) -> None:
            self.closed = True

    def first() -> Iterator[Engine]:
        yield Engine()
        log.append("first")
        raise OSError("first failed")

    def second() -> Iterator[Store]:
        yield Store(Engine())
        log.append("second")

    registry = RecordingRegistry()
    registry.resource(first, lifetime=Lifetime.PROCESS)
    registry.resource(second, lifetime=Lifetime.PROCESS)
    registry.context(port(Closable), lifetime=Lifetime.PROCESS)
    borrowed = Closable()
    process = build_container(
        registry, HUB_LIFETIMES, owner="hub", values=ScopeValues((supply(port(Closable), borrowed),))
    )
    process.resolver.get(Engine)
    process.resolver.get(Store)
    process.resolver.get(Closable)
    with pytest.raises(ExceptionGroup) as group:
        process.close()
    assert sorted(log) == ["first", "second"]
    assert [type(error) for error in group.value.exceptions] == [OSError]
    assert not borrowed.closed


def test_a_missing_context_value_fails_resolution_and_an_unregistered_one_is_refused() -> None:
    registry, _ = _hub()
    process = _open_hub(registry)
    tenant = open_child(process, lifetime=Lifetime.TENANT, owner="t", values=ScopeValues())
    with pytest.raises(Exception, match="StoreScope"):
        tenant.resolver.get(TenantService)
    with pytest.raises(ScopeValuesRefused):
        open_child(
            process, lifetime=Lifetime.TENANT, owner="t2", values=ScopeValues((supply(port(Principal), Principal()),))
        )
    with pytest.raises(ScopeValuesRefused):
        build_container(
            registry, HUB_LIFETIMES, owner="hub", values=ScopeValues((supply(port(Principal), Principal()),))
        )
    tenant.close()
    process.close()


def test_the_suite_turns_deprecation_warnings_into_errors_and_reaches_dishka() -> None:
    config = tomllib.loads((Path(__file__).resolve().parents[5] / "pyproject.toml").read_text())
    assert "error::DeprecationWarning" in config["tool"]["pytest"]["ini_options"]["filterwarnings"]
    registry = RecordingRegistry()
    registry.context(port(Engine), lifetime=Lifetime.COMMAND)
    owner = build_container(
        registry, COMMAND_LIFETIMES, owner="c", values=ScopeValues((supply(port(Engine), Engine()),))
    )
    raw: Container = owner._container  # pyright: ignore[reportPrivateUsage]  # the deprecated read lives on the raw container
    with pytest.raises(DeprecationWarning):
        _ = raw.context


def test_a_closed_owner_after_build_refuses_through_the_translation() -> None:
    registry = RecordingRegistry()
    registry.service(Engine, lifetime=Lifetime.PROCESS)
    process = build_container(registry, HUB_LIFETIMES, owner="hub", values=HUB)
    process.close()
    with pytest.raises(ScopeClosed):
        process.resolver.get(Engine)

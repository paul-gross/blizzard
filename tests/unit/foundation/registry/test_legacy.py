"""The legacy bridge: leaves and bags build under strict validation, and nothing but a bag depends on a bag."""

from collections.abc import Callable
from dataclasses import dataclass

import pytest

from blizzard.foundation.registry.interface import Lifetime, port
from blizzard.foundation.registry.internal.recording import Kind
from blizzard.foundation.registry.internal.translation import HUB_LIFETIMES, GraphRefused, build_container
from blizzard.foundation.registry.legacy import LegacyRegistry
from blizzard.foundation.registry.scopes import ScopeValues


class Clock:
    pass


class Store:
    def __init__(self, clock: Clock) -> None:
        self.clock = clock


class Settings:
    pass


@dataclass(frozen=True)
class State:
    store: Store
    settings: Settings


class Consumer:
    def __init__(self, state: State) -> None:
        self.state = state


def _settings() -> Settings:
    return Settings()


def _state(store: Store, settings: Settings) -> State:
    return State(store, settings)


def _registry() -> LegacyRegistry:
    registry = LegacyRegistry()
    registry.service(Clock, lifetime=Lifetime.PROCESS)
    registry.service(Store, lifetime=Lifetime.PROCESS)
    registry.leaf(_settings, lifetime=Lifetime.PROCESS)
    return registry


def test_a_bag_holds_the_same_instances_each_provider_resolves() -> None:
    registry = _registry()
    registry.bag(_state, lifetime=Lifetime.PROCESS)
    process = build_container(registry, HUB_LIFETIMES, owner="hub", values=ScopeValues())
    state = process.resolver.get(State)
    assert state.store is process.resolver.get(Store)
    assert state.settings is process.resolver.get(Settings)
    assert [e.kind for e in registry.entries if e.kind in (Kind.LEAF, Kind.BAG)] == [Kind.LEAF, Kind.BAG]
    process.close()


def test_a_bag_may_depend_on_another_bag() -> None:
    registry = _registry()
    registry.bag(_state, lifetime=Lifetime.PROCESS)

    def wrapped(state: State) -> Consumer:
        return Consumer(state)

    registry.bag(wrapped, lifetime=Lifetime.PROCESS)
    build_container(registry, HUB_LIFETIMES, owner="hub", values=ScopeValues()).close()


def test_every_other_registration_that_depends_on_a_bag_is_refused() -> None:
    def factory(state: State) -> Consumer:
        return Consumer(state)

    def leaf(state: State) -> Consumer:
        return Consumer(state)

    declarations: list[Callable[[LegacyRegistry], object]] = [
        lambda r: r.service(Consumer, lifetime=Lifetime.PROCESS),
        lambda r: r.factory(factory, lifetime=Lifetime.PROCESS),
        lambda r: r.leaf(leaf, lifetime=Lifetime.PROCESS),
    ]
    for declare in declarations:
        registry = _registry()
        registry.bag(_state, lifetime=Lifetime.PROCESS)
        declare(registry)
        with pytest.raises(GraphRefused, match="only a bag may depend on a bag"):
            build_container(registry, HUB_LIFETIMES, owner="refused", values=ScopeValues())
    aliased = _registry()
    bag = aliased.bag(_state, lifetime=Lifetime.PROCESS)
    aliased.alias(port(object), bag)
    with pytest.raises(GraphRefused, match="only a bag may depend on a bag"):
        build_container(aliased, HUB_LIFETIMES, owner="alias", values=ScopeValues())

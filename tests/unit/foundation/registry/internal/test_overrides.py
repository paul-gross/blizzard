"""Overrides replace an owned registration before validation and nothing after."""

from collections.abc import Iterator
from typing import Protocol

import pytest

from blizzard.foundation.registry.interface import Lifetime, Registry, port
from blizzard.foundation.registry.internal.overrides import OverrideSet
from blizzard.foundation.registry.internal.recording import RecordingRegistry, RegistrationRefused
from blizzard.foundation.registry.internal.translation import HUB_LIFETIMES, GraphRefused, ScopeValues, build_container


class Store:
    def __init__(self, label: str = "real") -> None:
        self.label = label


class IStore(Protocol):
    label: str


class Client:
    def __init__(self, store: Store) -> None:
        self.store = store


def _store() -> Store:
    return Store("real")


def _build(registry: RecordingRegistry, overrides: OverrideSet | None):
    return build_container(registry, HUB_LIFETIMES, owner="hub", values=ScopeValues(), overrides=overrides)


def test_a_replaced_factory_keeps_key_lifetime_and_alias_identity() -> None:
    registry = RecordingRegistry()
    store = registry.factory(_store, lifetime=Lifetime.PROCESS)
    registry.alias(port(IStore), store)
    registry.service(Client, lifetime=Lifetime.PROCESS)
    overrides = OverrideSet(registry)
    overrides.replace_factory(store, lambda: Store("fake"))
    process = _build(registry, overrides)
    assert process.resolver.get(Store).label == "fake"
    assert process.resolver.get(IStore) is process.resolver.get(Store)
    assert process.resolver.get(Client).store is process.resolver.get(Store)
    process.close()


def test_a_replacement_value_is_borrowed_and_stays_open_at_closure() -> None:
    closed: list[str] = []

    class Closing(Store):
        def close(self) -> None:
            closed.append("closed")

    def open_store() -> Iterator[Store]:
        yield Store()
        closed.append("original finalizer")

    registry = RecordingRegistry()
    store = registry.resource(open_store, lifetime=Lifetime.PROCESS)
    overrides = OverrideSet(registry)
    supplied = Closing("supplied")
    overrides.replace_value(store, supplied)
    process = _build(registry, overrides)
    assert process.resolver.get(Store) is supplied
    process.close()
    assert closed == []


def test_unknown_and_non_owned_targets_are_refused() -> None:
    registry = RecordingRegistry()
    overrides = OverrideSet(registry)
    foreign = RecordingRegistry().service(Store, lifetime=Lifetime.PROCESS)
    with pytest.raises(RegistrationRefused, match="did not issue"):
        overrides.replace_value(foreign, Store())
    context = registry.context(port(Client), lifetime=Lifetime.PROCESS)
    with pytest.raises(RegistrationRefused, match="context"):
        overrides.replace_value(context, Client(Store()))
    optional = registry.optional(port(Store), None, lifetime=Lifetime.PROCESS)
    with pytest.raises(RegistrationRefused, match="optional"):
        overrides.replace_value(optional, None)


def test_replacement_after_the_container_is_built_is_refused() -> None:
    registry = RecordingRegistry()
    store = registry.factory(_store, lifetime=Lifetime.PROCESS)
    overrides = OverrideSet(registry)
    _build(registry, overrides).close()
    with pytest.raises(RegistrationRefused, match="already built"):
        overrides.replace_value(store, Store())
    with pytest.raises(RegistrationRefused, match="already built"):
        overrides.replace_factory(store, _store)


def test_an_unreplaced_duplicate_owner_still_fails() -> None:
    registry = RecordingRegistry()
    first = registry.factory(_store, lifetime=Lifetime.PROCESS)
    registry.factory(_store, lifetime=Lifetime.PROCESS)
    overrides = OverrideSet(registry)
    overrides.replace_value(first, Store("fake"))
    with pytest.raises(GraphRefused):
        _build(registry, overrides)


def test_registry_offers_no_replacement() -> None:
    assert not [name for name in dir(Registry) if name.startswith("replace")]
    assert not [name for name in dir(RecordingRegistry) if name.startswith("replace")]

"""The recorder refuses what it can judge alone."""

from collections.abc import Iterator
from typing import Any, Protocol

import pytest

from blizzard.foundation.registry.interface import Lifetime, Port, Ref, port
from blizzard.foundation.registry.internal.recording import Kind, RecordingRegistry, RegistrationRefused


class _IRead(Protocol):
    def read(self) -> str: ...


class _IWrite(Protocol):
    def write(self) -> None: ...


class _Store:
    def read(self) -> str:
        return ""


def _make() -> _Store:
    return _Store()


def _open() -> Iterator[_Store]:
    yield _Store()


def test_each_owned_registration_gets_its_own_ref() -> None:
    registry = RecordingRegistry()
    first = registry.service(_Store, lifetime=Lifetime.PROCESS)
    second = registry.service(_Store, lifetime=Lifetime.PROCESS)
    assert first != second
    assert registry.entry_for(first).key is _Store
    assert [entry.kind for entry in registry.entries] == [Kind.SERVICE, Kind.SERVICE]


def test_factory_and_resource_key_by_their_annotations() -> None:
    registry = RecordingRegistry()
    made = registry.factory(_make, lifetime=Lifetime.PROCESS)
    opened = registry.resource(_open, lifetime=Lifetime.PROCESS)
    assert registry.entry_for(made).key is _Store
    assert registry.entry_for(opened).key is _Store


def test_a_generator_is_not_a_factory_and_a_plain_function_is_not_a_resource() -> None:
    registry = RecordingRegistry()
    with pytest.raises(RegistrationRefused, match="generator"):
        registry.factory(_open, lifetime=Lifetime.PROCESS)
    with pytest.raises(RegistrationRefused, match="not a generator"):
        registry.resource(_make, lifetime=Lifetime.PROCESS)  # pyright: ignore[reportArgumentType]  # refused at run time


def test_a_foreign_ref_is_refused() -> None:
    registry = RecordingRegistry()
    with pytest.raises(RegistrationRefused, match="did not issue"):
        registry.alias(port(_Store), Ref[_Store](key=object()))


def test_a_ref_from_another_registry_is_refused() -> None:
    other = RecordingRegistry().service(_Store, lifetime=Lifetime.PROCESS)
    with pytest.raises(RegistrationRefused, match="did not issue"):
        RecordingRegistry().alias(port(_Store), other)


def test_a_directly_built_port_is_refused() -> None:
    registry = RecordingRegistry()
    with pytest.raises(RegistrationRefused, match=r"port\(\) did not build"):
        registry.context(Port(_Store), lifetime=Lifetime.PROCESS)


def test_a_port_whose_parameter_differs_from_its_annotation_is_refused() -> None:
    registry = RecordingRegistry()
    mismatched: Any = Port[_IRead](_IWrite)  # pyright: ignore[reportArgumentType]  # built outside typing
    mismatched._issued = True
    with pytest.raises(RegistrationRefused, match="differs"):
        registry.context(mismatched, lifetime=Lifetime.PROCESS)


def test_an_alias_onto_a_non_owner_is_refused() -> None:
    registry = RecordingRegistry()
    optional = registry.optional(port(_Store), None, lifetime=Lifetime.PROCESS)
    with pytest.raises(RegistrationRefused, match="owns an instance"):
        registry.alias(port(_Store), optional)  # pyright: ignore[reportArgumentType]  # refused at run time

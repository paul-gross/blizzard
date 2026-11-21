"""The interface's vocabulary: ``port`` is the one constructor, a ``Ref`` is nominal, ``Registry`` replaces nothing."""

from typing import Protocol, get_type_hints

from blizzard.foundation.registry.interface import Lifetime, Port, Ref, Registry, ScopeClosed, ServiceAbsent, port


class _IRead(Protocol):
    def read(self) -> str: ...


def test_port_carries_its_annotation_and_is_issued() -> None:
    built = port(_IRead)
    assert built.annotation is _IRead
    assert built.issued


def test_a_directly_built_port_is_not_issued() -> None:
    assert not Port(_IRead).issued


def test_a_subscripted_port_remembers_its_declared_parameter() -> None:
    built = Port[_IRead](int)  # pyright: ignore[reportArgumentType]  # the mismatch the translation refuses
    assert built.__orig_class__ is not None


def test_a_ref_is_a_frozen_opaque_handle() -> None:
    assert Ref[object](key="k") == Ref[object](key="k")


def test_registry_has_no_replacement_operation() -> None:
    members = set(get_type_hints(Registry)) | {name for name in dir(Registry) if not name.startswith("_")}
    assert not [name for name in members if name.startswith("replace")]
    assert {"service", "factory", "resource", "context", "alias", "absent", "optional"} <= members


def test_the_lifetimes_are_the_declared_six() -> None:
    assert {lifetime.name for lifetime in Lifetime} == {
        "PROCESS",
        "TENANT",
        "REQUEST",
        "RUNNER",
        "COMMAND",
        "OPERATION",
    }


def test_the_refusals_carry_their_facts() -> None:
    assert ServiceAbsent("clock").label == "clock"
    closed = ScopeClosed(Lifetime.TENANT, "t1")
    assert (closed.lifetime, closed.owner) == (Lifetime.TENANT, "t1")

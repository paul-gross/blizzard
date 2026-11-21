"""The registration vocabulary every area's wiring imports: handles, ports, lifetimes, and the ``Registry`` protocol.

Nothing here knows a container. A process root hands each area a ``Registry``; the translation behind it builds the
validated graph. ``Ref`` identifies a registration the registry issued, ``Port`` names a runtime annotation a root
supplies or aliases, and ``port`` is the only way to build one."""

from __future__ import annotations

from collections.abc import Callable, Iterator
from dataclasses import dataclass
from enum import Enum
from typing import Final, Generic, Protocol, TypeVar

from blizzard.foundation.roles import domain_model

T_co = TypeVar("T_co", covariant=True)

#: Declared invariant and never inferred, so ``Port[IRead]`` is neither a ``Port[IWrite]`` nor a ``Port[object]``.
T_port = TypeVar("T_port")

_ISSUER: Final = object()


@domain_model
@dataclass(frozen=True)
class Ref(Generic[T_co]):  # noqa: UP046  # covariance is declared, not inferred from an unused parameter
    """An opaque handle to one registration the registry issued; not a live instance."""

    key: object


class Port(Generic[T_port]):  # noqa: UP046  # invariance is declared, never inferred
    """The constructor annotation a root supplies or aliases, built only through ``port``.

    Instantiating a subscripted ``Port[IRead](IWrite)`` leaves ``__orig_class__`` behind, which the translation reads to
    refuse a port whose type parameter differs from its annotation."""

    __slots__ = ("__orig_class__", "_annotation", "_issued")

    def __init__(self, annotation: type[T_port], issuer: object = None) -> None:
        self._annotation = annotation
        self._issued = issuer is _ISSUER

    @property
    def annotation(self) -> type[T_port]:
        """The annotation this port resolves under."""
        return self._annotation

    @property
    def issued(self) -> bool:
        """Whether ``port`` built this instance."""
        return self._issued

    def __repr__(self) -> str:
        return f"Port({self._annotation!r})"


def port[T](annotation: type[T]) -> Port[T]:
    """The one ``Port`` constructor; ``T`` is inferred from the annotation."""
    return Port(annotation, _ISSUER)


class Lifetime(Enum):
    """How long a registration's instance lives; each process maps the lifetimes it uses onto its own scopes."""

    PROCESS = "process"
    TENANT = "tenant"
    REQUEST = "request"
    RUNNER = "runner"
    COMMAND = "command"
    OPERATION = "operation"


class Registry(Protocol):
    """What an area's wiring declares itself through. It has no replacement operation."""

    def service[T](self, source: type[T], *, lifetime: Lifetime) -> Ref[T]:
        """Register a concrete class, its constructor parameters resolved by their annotations."""
        ...

    def factory[T](self, build: Callable[..., T], *, lifetime: Lifetime) -> Ref[T]:
        """Register a function keyed by its return annotation; every parameter annotated, none defaulted."""
        ...

    def resource[T](self, open: Callable[..., Iterator[T]], *, lifetime: Lifetime) -> Ref[T]:
        """Register a one-yield generator whose finalizer the declared lifetime owns."""
        ...

    def context[T](self, port: Port[T], *, lifetime: Lifetime) -> Ref[T]:
        """Declare a borrowed value supplied when ``lifetime`` opens, never finalized here."""
        ...

    def alias[T](self, port: Port[T], target: Ref[T]) -> None:
        """Resolve the port's annotation to the target registration, adding no construction or finalizer."""
        ...

    def absent[T](self, port: Port[T], *, lifetime: Lifetime, label: str) -> None:
        """Register the port as deliberately missing: resolving it raises ``ServiceAbsent(label)``."""
        ...

    def optional[T](self, port: Port[T], target: Ref[T] | None, *, lifetime: Lifetime) -> Ref[T | None]:
        """Register ``T | None``: the target's instance when the root has it, else ``None``. Owns nothing."""
        ...


class ServiceAbsent(Exception):
    """Raised by resolving an ``absent`` registration."""

    def __init__(self, label: str) -> None:
        super().__init__(label)
        self.label = label


class ScopeClosed(Exception):
    """Raised by resolving through a closing or closed owner, carrying its lifetime and id."""

    def __init__(self, lifetime: Lifetime, owner: str) -> None:
        super().__init__(f"the {lifetime.value} scope {owner!r} is closed")
        self.lifetime = lifetime
        self.owner = owner

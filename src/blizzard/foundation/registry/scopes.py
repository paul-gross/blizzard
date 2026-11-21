"""The one door to a child scope: ``open_child`` opens the next lifetime beneath an owned parent.

A process root builds its ``OwnedChild`` through the translation; everything else holds only the two Protocols here and
opens children through ``open_child``, so a child is only ever opened over the locked owner chain."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol, overload

from blizzard.foundation.registry.interface import Lifetime, Port
from blizzard.foundation.registry.internal.recording import annotation_of
from blizzard.foundation.registry.internal.resolver import ContainerOwner
from blizzard.foundation.roles import domain_model


class Resolver(Protocol):
    """Synchronous resolution that refuses a closing or closed owner with ``ScopeClosed``."""

    @overload
    def get[T](self, dependency_type: type[T], component: str | None = "") -> T: ...

    @overload
    def get(self, dependency_type: Any, component: str | None = "") -> Any: ...


class OwnedChild(Protocol):
    """One open scope: its lifetime and owner id, a guarded resolver, and a close that runs finalizers once."""

    @property
    def lifetime(self) -> Lifetime:
        """The lifetime this scope serves."""
        ...

    @property
    def owner(self) -> str:
        """The owner's id: a tenant id, a runner id, or a process label."""
        ...

    @property
    def resolver(self) -> Resolver:
        """The guarded resolver over this scope."""
        ...

    def close(self, exception: BaseException | None = None) -> None:
        """Close once: wait out active resolutions, then finalize in reverse construction order."""
        ...


@domain_model
@dataclass(frozen=True)
class ScopeValue[T]:
    """One borrowed value for a context port."""

    port: Port[T]
    value: T


@domain_model
@dataclass(frozen=True)
class ScopeValues:
    """Typed ``(Port[T], T)`` pairs supplied when a lifetime opens; checked against the contexts registered there."""

    items: tuple[ScopeValue[Any], ...] = ()

    def as_context(self) -> dict[object, object]:
        """The values keyed by the annotation each port resolves under."""
        return {annotation_of(item.port): item.value for item in self.items}


def supply[T](port: Port[T], value: T) -> ScopeValue[T]:
    """One typed value for ``port``."""
    return ScopeValue(port, value)


def open_child(parent: OwnedChild, *, lifetime: Lifetime, owner: str, values: ScopeValues) -> OwnedChild:
    """Open ``lifetime`` beneath ``parent`` with ``values`` for its registered contexts; never closes ``parent``.

    ``lifetime`` must be the one following the parent's; a value for a port the lifetime did not register as context is
    refused. A parent that is closing or closed raises ``ScopeClosed``."""
    if not isinstance(parent, ContainerOwner):
        raise TypeError(f"only an owner this registry built is a parent, not {type(parent).__name__}")
    return parent.open_child(lifetime=lifetime, owner=owner, values=values.as_context())

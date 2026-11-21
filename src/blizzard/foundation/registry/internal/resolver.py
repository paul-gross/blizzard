"""Guarded synchronous resolution over raw dishka containers, which never leave this package.

``OwnerGuard`` serializes lookup with closure without serializing lookups with each other: it is shared for resolution
and exclusive for closing, and a close in progress refuses new resolutions at once. ``ContainerOwner`` owns one raw
container and its place in a parent chain; ``GuardedResolver`` is the injection-facing handle a request, driver, or
operation resolves through."""

from __future__ import annotations

import threading
from collections.abc import Mapping, Sequence
from typing import Any, cast, overload

from dishka import Container
from dishka.exceptions import ExitError

from blizzard.foundation.registry.interface import Lifetime, ScopeClosed


class OwnerGuard:
    """Shared for resolution, exclusive for closing, writer-preferring."""

    def __init__(self) -> None:
        self._condition = threading.Condition()
        self._readers = 0
        self._closing = False

    @property
    def closing(self) -> bool:
        """Whether a close has begun, so no further resolution is admitted."""
        with self._condition:
            return self._closing

    def enter_shared(self) -> bool:
        """Admit one resolution; ``False`` once a close has begun."""
        with self._condition:
            if self._closing:
                return False
            self._readers += 1
            return True

    def exit_shared(self) -> None:
        """Release one admitted resolution."""
        with self._condition:
            self._readers -= 1
            if self._readers == 0:
                self._condition.notify_all()

    def begin_close(self) -> bool:
        """Refuse new resolutions, then wait out the active ones; ``False`` when a close already began."""
        with self._condition:
            if self._closing:
                return False
            self._closing = True
            self._condition.wait_for(lambda: self._readers == 0)
            return True


class ContainerOwner:
    """One raw container, the lifetime it serves, its owner id, and its parent chain."""

    def __init__(
        self,
        container: Container,
        *,
        lifetimes: tuple[Lifetime, ...],
        owner: str,
        parent: ContainerOwner | None = None,
    ) -> None:
        self._container = container
        self._lifetimes = lifetimes
        self._depth = 0 if parent is None else parent._depth + 1
        self._owner = owner
        self._parent = parent
        self._guard = OwnerGuard()

    @property
    def lifetime(self) -> Lifetime:
        """The lifetime this owner serves."""
        return self._lifetimes[self._depth]

    @property
    def owner(self) -> str:
        """The owner's id: a tenant id, a runner id, or a process label."""
        return self._owner

    @property
    def parent(self) -> ContainerOwner | None:
        """The owner this one was opened beneath, if any."""
        return self._parent

    @property
    def resolver(self) -> GuardedResolver:
        """The guarded resolver over this owner."""
        return GuardedResolver(self)

    def open_child(self, *, lifetime: Lifetime, owner: str, values: Mapping[object, object]) -> ContainerOwner:
        """Open the next lifetime beneath this owner with a locked container; never closes this owner."""
        following = self._depth + 1
        if following >= len(self._lifetimes) or self._lifetimes[following] is not lifetime:
            raise ValueError(f"the {self.lifetime.value} scope opens only its next lifetime, not {lifetime.value}")
        if self._guard.closing:
            raise ScopeClosed(self.lifetime, self._owner)
        child = self._container(context=dict(values), lock_factory=threading.Lock)
        return ContainerOwner(child, lifetimes=self._lifetimes, owner=owner, parent=self)

    def close(self, exception: BaseException | None = None) -> None:
        """Close once: wait out active resolutions, then finalize outside the guards; later calls do nothing."""
        if not self._guard.begin_close():
            return
        try:
            self._container.close(exception)
        except ExitError as error:
            failures: Sequence[Exception] = cast("Sequence[Exception]", error.exceptions)  # pyright: ignore[reportUnknownMemberType]  # dishka leaves ExitError.exceptions untyped
            raise ExceptionGroup(str(error), list(failures)) from None

    def get(self, dependency_type: Any, component: str | None = "") -> Any:
        """Resolve through every owner's shared guard, ancestor to child; refuse the first closing owner on the path."""
        held: list[ContainerOwner] = []
        try:
            for owner in self._chain():
                if not owner._guard.enter_shared():
                    raise ScopeClosed(owner.lifetime, owner.owner)
                held.append(owner)
            return self._container.get(dependency_type, component)
        finally:
            for owner in reversed(held):
                owner._guard.exit_shared()

    def _chain(self) -> list[ContainerOwner]:
        chain: list[ContainerOwner] = []
        owner: ContainerOwner | None = self
        while owner is not None:
            chain.append(owner)
            owner = owner._parent
        chain.reverse()
        return chain


class GuardedResolver:
    """The injection-facing handle: synchronous ``get`` that refuses a closing or closed owner."""

    def __init__(self, owner: ContainerOwner) -> None:
        self._owner = owner

    @overload
    def get[T](self, dependency_type: type[T], component: str | None = "") -> T: ...

    @overload
    def get(self, dependency_type: Any, component: str | None = "") -> Any: ...

    def get(self, dependency_type: Any, component: str | None = "") -> Any:
        """Resolve ``dependency_type``, forwarding ``component`` unchanged to the raw container."""
        return self._owner.get(dependency_type, component)

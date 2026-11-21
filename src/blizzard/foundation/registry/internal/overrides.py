"""Test-only replacement of an existing owned registration, applied before the graph is validated.

The set is built from one ``RecordingRegistry`` at the translation edge and is never reachable from ``Registry``. A
replacement keeps the target's key and lifetime, so aliases keep resolving through it. It substitutes the recorded entry
rather than asking the container to override, so a duplicate owner nobody replaced still fails validation."""

from __future__ import annotations

import inspect
from collections.abc import Callable
from dataclasses import replace
from typing import Any, Protocol

from blizzard.foundation.registry.interface import Ref
from blizzard.foundation.registry.internal.recording import (
    OWNING,
    Kind,
    RecordingRegistry,
    Registration,
    RegistrationRefused,
)


class TestOverrides(Protocol):
    """What a test replaces: an owned registration's construction, or its instance."""

    def replace_factory[T](self, target: Ref[T], build: Callable[..., T]) -> None:
        """Construct the target with ``build`` instead."""
        ...

    def replace_value[T](self, target: Ref[T], value: T) -> None:
        """Supply ``value`` as the target; it is borrowed, so no finalizer closes it."""
        ...


class OverrideSet:
    """The replacements for one recorded registry, sealed once a container is built from them."""

    def __init__(self, recorded: RecordingRegistry) -> None:
        self._recorded = recorded
        self._replacements: dict[object, Registration] = {}
        self._sealed = False

    def replace_factory[T](self, target: Ref[T], build: Callable[..., T]) -> None:
        """Construct the target with ``build``; a generator function replaces it with an owned resource."""
        entry = self._target(target)
        kind = Kind.RESOURCE if inspect.isgeneratorfunction(build) else Kind.FACTORY
        self._replacements[entry.token] = replace(entry, kind=kind, source=build)

    def replace_value[T](self, target: Ref[T], value: T) -> None:
        """Supply ``value`` as the target, borrowed."""
        entry = self._target(target)
        self._replacements[entry.token] = replace(entry, kind=Kind.VALUE, source=value)

    def apply(self, entries: tuple[Registration, ...]) -> tuple[Registration, ...]:
        """The entries with replacements substituted; afterwards the set refuses any further replacement."""
        self._sealed = True
        return tuple(self._replacements.get(entry.token, entry) for entry in entries)

    def _target(self, target: Ref[Any]) -> Registration:
        if self._sealed:
            raise RegistrationRefused("a container was already built from these overrides")
        entry = self._recorded.entry_for(target)
        if entry.kind not in OWNING or entry.kind is Kind.CONTEXT:
            raise RegistrationRefused(
                f"only an owned service, factory, or resource is replaced, not {entry.kind.value}"
            )
        return entry

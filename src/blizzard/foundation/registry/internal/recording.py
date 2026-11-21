"""The one ``Registry`` implementation: it records each registration as an immutable entry for the translation.

Recording refuses what it can judge alone: a ``Ref`` it did not issue, an alias onto a registration that owns nothing,
and a ``Port`` that ``port`` did not build or whose type parameter differs from its annotation. Judging the whole graph
(duplicate owners, missing dependencies, cycles, lifetimes) is the translation's."""

from __future__ import annotations

import inspect
from collections.abc import Callable, Iterator, Mapping
from dataclasses import dataclass
from enum import Enum
from typing import Any, Protocol, cast, get_args, get_type_hints

from blizzard.foundation.registry.interface import Lifetime, Port, Ref
from blizzard.foundation.roles import domain_model


class RegistrationRefused(Exception):
    """A registration the recorder rejects before any graph is built."""


class Kind(Enum):
    """What a recorded registration constructs, supplies, or maps."""

    SERVICE = "service"
    FACTORY = "factory"
    RESOURCE = "resource"
    CONTEXT = "context"
    ALIAS = "alias"
    ABSENT = "absent"
    OPTIONAL = "optional"
    VALUE = "value"


#: The kinds that own an instance, so an alias or an override may target them.
OWNING = frozenset({Kind.SERVICE, Kind.FACTORY, Kind.RESOURCE, Kind.CONTEXT})


@domain_model
@dataclass(frozen=True)
class Registration:
    """One recorded registration. ``source`` is the class, function, generator, or supplied value it builds from."""

    token: object
    key: object
    lifetime: Lifetime | None
    kind: Kind
    source: Any = None
    target_key: object = None
    label: str = ""


class Rewrites(Protocol):
    """Replacements applied to the recorded entries once, immediately before the graph is built."""

    def apply(self, entries: tuple[Registration, ...]) -> tuple[Registration, ...]:
        """The entries with each replacement substituted, then the set sealed against further replacement."""
        ...


class RecordingRegistry:
    """Records registrations in order, issuing a fresh ``Ref`` per owned one."""

    def __init__(self) -> None:
        self._entries: list[Registration] = []
        self._issued: dict[object, Registration] = {}

    @property
    def entries(self) -> tuple[Registration, ...]:
        """Every recorded registration, in the order declared."""
        return tuple(self._entries)

    def entry_for(self, ref: Ref[Any]) -> Registration:
        """The registration ``ref`` identifies; refuses a ``Ref`` this registry did not issue."""
        entry = self._issued.get(ref.key)
        if entry is None:
            raise RegistrationRefused(f"a Ref this registry did not issue: {ref!r}")
        return entry

    def service[T](self, source: type[T], *, lifetime: Lifetime) -> Ref[T]:
        return self._own(Kind.SERVICE, source, lifetime, source)

    def factory[T](self, build: Callable[..., T], *, lifetime: Lifetime) -> Ref[T]:
        if inspect.isgeneratorfunction(build):
            raise RegistrationRefused(f"{_name(build)} is a generator: register it as a resource")
        hints = _hints(build)
        if "return" not in hints:
            raise RegistrationRefused(f"{_name(build)} has no return annotation to key its registration by")
        return self._own(Kind.FACTORY, hints["return"], lifetime, build)

    def resource[T](self, open: Callable[..., Iterator[T]], *, lifetime: Lifetime) -> Ref[T]:
        if not inspect.isgeneratorfunction(open):
            raise RegistrationRefused(f"{_name(open)} is not a generator function")
        hints = _hints(open)
        declared = hints.get("return")
        arguments = get_args(declared)
        if not arguments:
            raise RegistrationRefused(
                f"{_name(open)} must be annotated to return Iterator[T] so T keys its registration"
            )
        return self._own(Kind.RESOURCE, arguments[0], lifetime, open)

    def context[T](self, port: Port[T], *, lifetime: Lifetime) -> Ref[T]:
        return self._own(Kind.CONTEXT, annotation_of(port), lifetime, None)

    def alias[T](self, port: Port[T], target: Ref[T]) -> None:
        annotation = annotation_of(port)
        entry = self.entry_for(target)
        if entry.kind not in OWNING:
            raise RegistrationRefused(f"an alias targets a registration that owns an instance, not {entry.kind.value}")
        self._entries.append(
            Registration(token=object(), key=annotation, lifetime=None, kind=Kind.ALIAS, target_key=entry.key)
        )

    def absent[T](self, port: Port[T], *, lifetime: Lifetime, label: str) -> None:
        self._entries.append(
            Registration(token=object(), key=annotation_of(port), lifetime=lifetime, kind=Kind.ABSENT, label=label)
        )

    def optional[T](self, port: Port[T], target: Ref[T] | None, *, lifetime: Lifetime) -> Ref[T | None]:
        annotation = annotation_of(port)
        target_key = None
        if target is not None:
            entry = self.entry_for(target)
            if entry.kind not in OWNING:
                raise RegistrationRefused(
                    f"an optional targets a registration that owns an instance, not {entry.kind.value}"
                )
            target_key = entry.key
        optional_key = cast("object", annotation | None)  # pyright: ignore[reportOperatorIssue]  # class union
        entry = Registration(
            token=object(), key=optional_key, lifetime=lifetime, kind=Kind.OPTIONAL, target_key=target_key
        )
        self._entries.append(entry)
        self._issued[entry.token] = entry
        return Ref(entry.token)

    def _own(self, kind: Kind, key: object, lifetime: Lifetime, source: Any) -> Ref[Any]:
        entry = Registration(token=object(), key=key, lifetime=lifetime, kind=kind, source=source)
        self._entries.append(entry)
        self._issued[entry.token] = entry
        return Ref(entry.token)


def merged(entries: tuple[Registration, ...], rewrites: Rewrites | None) -> tuple[Registration, ...]:
    """The entries after ``rewrites``, or unchanged when there are none."""
    return entries if rewrites is None else rewrites.apply(entries)


def keys_by_lifetime(entries: tuple[Registration, ...], kind: Kind) -> Mapping[Lifetime, frozenset[object]]:
    """The keys of ``kind`` entries, grouped by the lifetime they are registered at."""
    grouped: dict[Lifetime, set[object]] = {}
    for entry in entries:
        if entry.kind is kind and entry.lifetime is not None:
            grouped.setdefault(entry.lifetime, set()).add(entry.key)
    return {lifetime: frozenset(keys) for lifetime, keys in grouped.items()}


def annotation_of(port: Port[Any]) -> object:
    """The key a ``Port`` registers under; refuses one ``port`` did not build or whose type parameter differs."""
    if not port.issued:
        raise RegistrationRefused(f"a Port that port() did not build: {port!r}")
    declared = getattr(port, "__orig_class__", None)
    if declared is not None and get_args(declared)[0] is not port.annotation:
        raise RegistrationRefused(f"a Port whose type parameter differs from its annotation: {port!r}")
    return port.annotation


def _hints(function: Callable[..., Any]) -> dict[str, Any]:
    try:
        return get_type_hints(function)
    except (NameError, TypeError) as error:
        raise RegistrationRefused(f"{_name(function)} has annotations that do not evaluate: {error}") from error


def _name(function: Callable[..., Any]) -> str:
    return getattr(function, "__qualname__", repr(function))

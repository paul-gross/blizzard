"""The resolved flag answer for one process, and how overrides resolve into it.

A process's :class:`Flags` is the registry's defaults with that process's ``[flags]`` overrides applied. The
resolution is pure: it reports each override it ignored with its reason, and leaves logging to the composition root."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, replace
from types import MappingProxyType
from typing import Literal

import structlog

from blizzard.foundation.flags.registry import FlagRegistry, FlagScope
from blizzard.foundation.roles import domain_model

#: Which side resolves: ``hub`` answers ``hub`` and ``fleet`` flags, ``runner`` answers ``runner`` flags.
type FlagSide = Literal["hub", "runner"]

_HUB_ANSWERS: frozenset[FlagScope] = frozenset({"hub", "fleet"})


def side_answers(side: FlagSide, scope: FlagScope) -> bool:
    """Whether ``side`` answers a flag of ``scope`` at all."""
    return scope in (_HUB_ANSWERS if side == "hub" else frozenset({"runner"}))


class UnknownFlag(LookupError):
    """A flag name the registry holds neither live nor retired — a mistyped name, never read as off."""


class FlagNotReadable(LookupError):
    """A live flag of the other side's scope — the hub's flag asked of a runner, or a runner's of the hub."""


class FlagOffError(LookupError):
    """A guarded behavior was reached while its flag is off. ``flag`` names the flag."""

    def __init__(self, flag: str) -> None:
        super().__init__(f"{flag!r} is off")
        self.flag = flag


@domain_model
@dataclass(frozen=True)
class IgnoredOverride:
    """One ``[flags]`` key a process did not apply, and why — the message a warning carries."""

    flag: str
    reason: str


@domain_model
@dataclass(frozen=True)
class Flags:
    """Every flag a process may read, resolved: its answer, and which of its answers an override changed."""

    #: Every name the registry holds, live or retired — what ``on`` accepts at all.
    known: frozenset[str]
    #: Retired names, which read on.
    retired: frozenset[str]
    #: Each live flag this process may read, as it stands.
    values: Mapping[str, bool]
    #: The names its published answer covers — a subset of ``values``.
    published: frozenset[str]
    #: Names whose applied override changed the registry's decision, whatever the value.
    overridden: tuple[str, ...] = ()
    #: The live ``fleet`` flags in the registry — those only the hub's answer turns on for a runner.
    fleet: frozenset[str] = frozenset()
    #: The retired ``fleet`` flags, which the hub's answer reports on.
    fleet_retired: frozenset[str] = frozenset()

    def on(self, name: str) -> bool:
        """Whether ``name`` is on. Raises for a name the registry does not hold, so a typo fails its first test."""
        if name in self.retired:
            return True
        if name not in self.known:
            raise UnknownFlag(f"{name!r} is not declared in the flag registry")
        if name not in self.values:
            raise FlagNotReadable(f"{name!r} is not a flag this process answers")
        return self.values[name]

    def require_on(self, name: str) -> None:
        """Raise :class:`FlagOffError` unless ``name`` is on; the name is checked as ``on`` checks it."""
        if not self.on(name):
            raise FlagOffError(name)

    def answer(self) -> dict[str, bool]:
        """The live flags this process publishes, each to whether it is on."""
        return {name: self.values[name] for name in sorted(self.published)}

    def fleet_answer(self) -> dict[str, bool]:
        """What the hub tells its runners about the ``fleet`` flags: each live one at its resolved value, and each
        retired one on, since a runner that predates the retirement still reads it."""
        answer = {name: self.values[name] for name in sorted(self.fleet) if name in self.values}
        answer.update(dict.fromkeys(sorted(self.fleet_retired), True))
        return answer

    def with_fleet_answer(self, answer: Mapping[str, bool]) -> Flags:
        """These flags with the hub's ``fleet`` ``answer`` applied: each live fleet flag takes the answer's value, a
        name the answer omits reads off, and a name the registry lacks is ignored. Runner-scope values and
        ``overridden`` are untouched."""
        values = dict(self.values)
        values.update({name: answer.get(name, False) for name in self.fleet})
        return replace(self, values=MappingProxyType(values))


@domain_model
@dataclass(frozen=True)
class FlagResolution:
    """A process's :class:`Flags` and the overrides it ignored."""

    flags: Flags
    ignored: tuple[IgnoredOverride, ...]


def resolve_flags(registry: FlagRegistry, side: FlagSide, overrides: Mapping[str, bool]) -> FlagResolution:
    """Resolve ``side``'s flags from the registry's defaults and its ``[flags]`` ``overrides``.

    A runner's ``fleet`` flags read off here: only its hub's answer turns one on, which a later layer applies."""
    answers = _HUB_ANSWERS if side == "hub" else frozenset({"runner"})
    values = {name: flag.default for name, flag in registry.live.items() if flag.scope in answers}
    if side == "runner":
        values.update({name: False for name, flag in registry.live.items() if flag.scope == "fleet"})
    published = frozenset(name for name, flag in registry.live.items() if flag.scope in answers)
    applied: list[str] = []
    ignored: list[IgnoredOverride] = []
    for name, value in overrides.items():
        reason = _refusal(registry, side, name)
        if reason is not None:
            ignored.append(IgnoredOverride(name, reason))
        else:
            values[name] = value
            applied.append(name)
    flags = Flags(
        known=frozenset(registry.live) | frozenset(registry.retired),
        retired=frozenset(registry.retired),
        values=MappingProxyType(values),
        published=published,
        overridden=tuple(sorted(applied)),
        fleet=frozenset(name for name, flag in registry.live.items() if flag.scope == "fleet"),
        fleet_retired=frozenset(name for name, scope in registry.retired.items() if scope == "fleet"),
    )
    return FlagResolution(flags=flags, ignored=tuple(ignored))


def _refusal(registry: FlagRegistry, side: FlagSide, name: str) -> str | None:
    """Why ``side`` ignores an override of ``name``; ``None`` when it applies."""
    if name in registry.retired:
        return "retired: the behavior is always on, so the key can be removed"
    flag = registry.live.get(name)
    if flag is None:
        return "no such flag: not declared in the flag registry"
    if side == "runner" and flag.scope == "fleet":
        return "fleet flag: owned by the hub, set it in the hub's configuration"
    if side == "hub" and flag.scope == "runner":
        return "runner flag: set it in the host's configuration"
    if side == "runner" and flag.scope == "hub":
        return "hub flag: set it in the hub's configuration"
    return None


def warn_ignored_flags(log: structlog.stdlib.BoundLogger, resolution: FlagResolution) -> None:
    """Warn once for each override ``resolution`` ignored, with the ``flag`` and the ``reason`` as fields."""
    for ignored in resolution.ignored:
        log.warning("flag override ignored", flag=ignored.flag, reason=ignored.reason)

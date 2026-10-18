"""The configuration keys a flag holds: resolving a file's ``[flags]`` before its guarded keys are read, and what
was held (``bzh:flag-hides-behavior``)."""

from __future__ import annotations

from dataclasses import dataclass

import structlog

from blizzard.foundation.config_keys import ConfigRegistry, ConfigView
from blizzard.foundation.flags.flags import FlagResolution, FlagSide, resolve_flags, side_answers
from blizzard.foundation.flags.overrides import parse_overrides
from blizzard.foundation.flags.registry import FlagRegistry
from blizzard.foundation.roles import domain_model


@domain_model
@dataclass(frozen=True)
class HeldKey:
    """One guarded key a file carried while its flag was off, and so read as absent."""

    key: str
    flag: str


def check_guarded_keys(registry: ConfigRegistry, flags: FlagRegistry, side: FlagSide) -> None:
    """Refuse a key declaration whose flag the registry lacks or ``side``'s daemon cannot answer at load: a hub key
    takes a ``hub`` or ``fleet`` flag, a host key a ``runner`` flag, because a fleet answer arrives after start."""
    for key in registry.keys:
        if key.flag is None:
            continue
        scope = flags.scope_of(key.flag)
        if scope is None:
            raise ValueError(f"config key {key.path!r} names flag {key.flag!r}, which the flag registry does not hold")
        if not side_answers(side, scope):
            raise ValueError(
                f"config key {key.path!r} names {scope} flag {key.flag!r}, which a {side} cannot read at load"
            )


def resolve_for_keys(
    document: ConfigView, side: FlagSide, flags: FlagRegistry, error: type[Exception]
) -> tuple[ConfigView, FlagResolution]:
    """``document`` with its guarded keys read against the ``[flags]`` it carries, resolved once here; ``error`` is
    raised for a malformed ``[flags]`` table. The resolution is what composition applies."""
    check_guarded_keys(document.registry, flags, side)
    resolution = resolve_flags(flags, side, parse_overrides(document, error))
    return document.holding(resolution.flags), resolution


def held_keys(document: ConfigView) -> tuple[HeldKey, ...]:
    """Every guarded key ``document`` and its tables read as absent, by key path."""
    held = document.held
    return tuple(HeldKey(key, flag) for key, flag in sorted(held.held.items())) if held is not None else ()


def warn_held_keys(log: structlog.stdlib.BoundLogger, held: tuple[HeldKey, ...]) -> None:
    """Warn once for each key ``held`` names, with the ``key`` and the ``flag`` as fields."""
    for entry in held:
        log.warning("config key ignored: its flag is off", key=entry.key, flag=entry.flag)

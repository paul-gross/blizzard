"""The configuration keys a flag holds: resolving a file's ``[flags]`` before its guarded keys are read, and what
was held (``bzh:flag-hides-behavior``)."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

import structlog

from blizzard.foundation.config_keys import SCALAR_TYPES, ConfigRegistry, ConfigView
from blizzard.foundation.flags.flags import FlagResolution, Flags, FlagSide, resolve_flags, side_answers
from blizzard.foundation.flags.overrides import parse_overrides
from blizzard.foundation.flags.registry import FlagRegistry
from blizzard.foundation.roles import domain_model
from blizzard.foundation.untyped import as_list, as_table


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
        if key.type not in SCALAR_TYPES:
            raise ValueError(f"config key {key.path!r} is a {key.type} and cannot be guarded; guard its scalar keys")
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


def held_keys(registry: ConfigRegistry, document: Mapping[str, Any], flags: Flags) -> tuple[HeldKey, ...]:
    """Every guarded key of ``registry`` that ``document`` carries while its flag is off in ``flags`` — each read as
    absent — by key path."""
    return tuple(
        HeldKey(key.path, key.flag)
        for key in sorted(registry.keys, key=lambda key: key.path)
        if key.flag is not None and not flags.on(key.flag) and _carries(document, key.path.split("."))
    )


def _carries(table: object, parts: list[str]) -> bool:
    """Whether ``table`` holds a value at the key path ``parts``; a segment marked ``name[]`` matches any table of the
    array at ``name``."""
    table = as_table(table)
    if table is None:
        return False
    head, *rest = parts
    name = head.removesuffix("[]")
    if name not in table:
        return False
    if not rest:
        return True
    value = table[name]
    entries = (as_list(value) or []) if head.endswith("[]") else [value]
    return any(_carries(entry, rest) for entry in entries)


def warn_held_keys(log: structlog.stdlib.BoundLogger, held: tuple[HeldKey, ...]) -> None:
    """Warn once for each key ``held`` names, with the ``key`` and the ``flag`` as fields."""
    for entry in held:
        log.warning("config key ignored: its flag is off", key=entry.key, flag=entry.flag)

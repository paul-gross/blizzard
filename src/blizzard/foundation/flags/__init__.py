"""Feature flags: the registry file's loader, and the resolved :class:`Flags` code reads a flag through."""

from blizzard.foundation.flags.flags import (
    FlagNotReadable,
    FlagOffError,
    FlagResolution,
    Flags,
    FlagSide,
    IgnoredOverride,
    UnknownFlag,
    resolve_flags,
    warn_ignored_flags,
)
from blizzard.foundation.flags.guard import FlagGuarded, without_off_fields
from blizzard.foundation.flags.overrides import parse_overrides
from blizzard.foundation.flags.registry import FlagRegistry, FlagRegistryError, FlagScope, LiveFlag

__all__ = [
    "FlagGuarded",
    "FlagNotReadable",
    "FlagOffError",
    "FlagRegistry",
    "FlagRegistryError",
    "FlagResolution",
    "FlagScope",
    "FlagSide",
    "Flags",
    "IgnoredOverride",
    "LiveFlag",
    "UnknownFlag",
    "parse_overrides",
    "resolve_flags",
    "warn_ignored_flags",
    "without_off_fields",
]

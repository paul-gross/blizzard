"""The flag registry fixture and flag resolver the flag-guard tests share."""

from __future__ import annotations

from blizzard.foundation.flags import FlagRegistry, Flags, FlagSide, resolve_flags

REGISTRY = """
[flag.hub-thing]
owner = "epic:release-engineering/dark-launch"
summary = "A hub capability"
scope = "hub"
default = "off"

[flag.runner-thing]
owner = "initiative:a-runner-thing"
summary = "A runner capability"
scope = "runner"
default = "off"
"""


def flags_for(side: FlagSide, **overrides: bool) -> Flags:
    return resolve_flags(FlagRegistry.parse(REGISTRY, "fixture"), side, overrides).flags

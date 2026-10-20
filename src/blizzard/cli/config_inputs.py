"""What a process's root reads once and hands to its config loader: the packaged flag registry, and whether config
reads are strict.

Every entry point — each CLI verb, and the OpenAPI export — calls these; nothing beneath a root reads them again."""

from __future__ import annotations

import os
from importlib.resources import files
from typing import Final

from blizzard.foundation.flags import FlagRegistry, FlagRegistryError

_REGISTRY_PACKAGE: Final = "blizzard.foundation.flags"
_REGISTRY_FILE: Final = "registry.toml"

#: Set to ``1`` and an undeclared config read raises; the test suite sets it for itself and every daemon it spawns.
STRICT_CONFIG_ENVIRONMENT_VARIABLE: Final = "BLIZZARD_STRICT_CONFIG"


def packaged_flag_registry() -> FlagRegistry:
    """The flag registry packaged with the wheel; :class:`FlagRegistryError` when the file cannot be read."""
    source = files(_REGISTRY_PACKAGE).joinpath(_REGISTRY_FILE)
    try:
        text = source.read_text()
    except OSError as error:
        raise FlagRegistryError(f"{source}: cannot read the flag registry: {error}") from error
    return FlagRegistry.parse(text, str(source))


def strict_config() -> bool:
    """Whether this process reads its config strictly, raising on an undeclared key."""
    return os.environ.get(STRICT_CONFIG_ENVIRONMENT_VARIABLE) == "1"

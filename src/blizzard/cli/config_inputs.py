"""What a process's root reads once and hands to its config loader: the packaged flag registry.

Every entry point — each CLI verb, and the OpenAPI export — calls these; nothing beneath a root reads them again."""

from __future__ import annotations

from importlib.resources import files
from typing import Final

from blizzard.foundation.flags import FlagRegistry, FlagRegistryError

_REGISTRY_PACKAGE: Final = "blizzard.foundation.flags"
_REGISTRY_FILE: Final = "registry.toml"


def packaged_flag_registry() -> FlagRegistry:
    """The flag registry packaged with the wheel; :class:`FlagRegistryError` when the file cannot be read."""
    source = files(_REGISTRY_PACKAGE).joinpath(_REGISTRY_FILE)
    try:
        text = source.read_text()
    except OSError as error:
        raise FlagRegistryError(f"{source}: cannot read the flag registry: {error}") from error
    return FlagRegistry.parse(text, str(source))

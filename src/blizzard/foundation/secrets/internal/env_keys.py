"""The environment key provider — generations read once at start."""

from __future__ import annotations

import base64
import binascii
from collections.abc import Mapping

from blizzard.foundation.secrets.internal.key_material import KEY_BYTES, generation_of
from blizzard.foundation.secrets.key_variables import KeyVariables
from blizzard.foundation.secrets.sealing import IRotatableKeyProvider, KeyGeneration


class EnvKeyProvider:
    """The ``variables.current`` variable is the current generation; ``variables.previous``, when set, is also
    readable."""

    def __init__(self, environ: Mapping[str, str], *, variables: KeyVariables, config_error: type[Exception]) -> None:
        self._current = _parse(environ, variables.current, config_error)
        previous = _parse(environ, variables.previous, config_error) if environ.get(variables.previous) else None
        self._by_id = {g.key_id: g for g in (self._current, previous) if g is not None}

    def current(self) -> KeyGeneration:
        return self._current

    def generation(self, key_id: str) -> KeyGeneration | None:
        return self._by_id.get(key_id)

    def available_ids(self) -> frozenset[str]:
        return frozenset(self._by_id)

    def mint(self) -> KeyGeneration:
        """The generations are the environment's, so rotation seals into the current one."""
        return self._current

    def promote(self, key_id: str) -> None:
        """The generations are the environment's, so there is nothing to move."""

    def prune(self, referenced: frozenset[str]) -> None:
        """The generations are the environment's, so there is nothing to drop."""


def _parse(environ: Mapping[str, str], variable: str, config_error: type[Exception]) -> KeyGeneration:
    """Never echoes the variable's content — only its name and the expected shape."""
    try:
        material = base64.b64decode(environ[variable], validate=True)
    except (binascii.Error, ValueError):
        raise config_error(f"{variable} is not valid base64") from None
    if len(material) != KEY_BYTES:
        raise config_error(f"{variable} must decode to {KEY_BYTES} bytes")
    return generation_of(material)


_env_key_provider_conforms: type[IRotatableKeyProvider] = EnvKeyProvider

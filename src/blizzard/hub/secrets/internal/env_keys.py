"""The ``BZ_HUB_SECRET_KEY`` key provider — generations read once at start."""

from __future__ import annotations

import base64
import binascii
from collections.abc import Mapping

from blizzard.hub.config import ConfigError
from blizzard.hub.domain.config.secrets import IRotatableHubKeyProvider, KeyGeneration
from blizzard.hub.secrets.internal.key_material import KEY_BYTES, generation_of

ENV_SECRET_KEY = "BZ_HUB_SECRET_KEY"
ENV_SECRET_KEY_PREVIOUS = "BZ_HUB_SECRET_KEY_PREVIOUS"


class EnvKeyProvider:
    """``BZ_HUB_SECRET_KEY`` is the current generation; ``_PREVIOUS``, when set, is also readable."""

    def __init__(self, environ: Mapping[str, str]) -> None:
        self._current = _parse(environ, ENV_SECRET_KEY)
        previous = _parse(environ, ENV_SECRET_KEY_PREVIOUS) if environ.get(ENV_SECRET_KEY_PREVIOUS) else None
        self._by_id = {g.key_id: g for g in (self._current, previous) if g is not None}

    def current(self) -> KeyGeneration:
        return self._current

    def generation(self, key_id: str) -> KeyGeneration | None:
        return self._by_id.get(key_id)

    def available_ids(self) -> frozenset[str]:
        return frozenset(self._by_id)

    def mint(self) -> KeyGeneration:
        return self._current

    def promote(self, key_id: str) -> None:
        """The generations are the environment's, so there is nothing to move."""

    def prune(self, referenced: frozenset[str]) -> None:
        """The generations are the environment's, so there is nothing to drop."""


def _parse(environ: Mapping[str, str], variable: str) -> KeyGeneration:
    """Never echoes the variable's content — only its name and the expected shape."""
    try:
        material = base64.b64decode(environ[variable], validate=True)
    except (binascii.Error, ValueError):
        raise ConfigError(f"{variable} is not valid base64") from None
    if len(material) != KEY_BYTES:
        raise ConfigError(f"{variable} must decode to {KEY_BYTES} bytes")
    return generation_of(material)


def _conforms_env_key_provider(x: EnvKeyProvider) -> IRotatableHubKeyProvider:
    return x

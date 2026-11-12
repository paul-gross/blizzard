"""The hub's bindings of the secret sealing — its key source, variable names, refusals, and reader.

The key is a process setting (``bzh:config-read-on-use`` Scope): ``BZ_HUB_SECRET_KEY``,
when set, is the only source; otherwise ``data/auth/secret-keys/`` holds the generations."""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path

from blizzard.foundation.secrets.factories import key_exists, key_provider
from blizzard.foundation.secrets.key_variables import KeyVariables
from blizzard.foundation.secrets.sealing import IRotatableKeyProvider, ISealedSecretRepository, ISecretCipher
from blizzard.foundation.secrets.value import SecretValue
from blizzard.hub.config import ConfigError
from blizzard.hub.domain.config.secrets import (
    ISecretCatalog,
    ISecretReader,
    SecretName,
    SecretNotFound,
    require_revealable,
)
from blizzard.hub.layout import SECRET_KEYS_PARTS

ENV_SECRET_KEY = "BZ_HUB_SECRET_KEY"
ENV_SECRET_KEY_PREVIOUS = "BZ_HUB_SECRET_KEY_PREVIOUS"
HUB_KEY_VARIABLES = KeyVariables(current=ENV_SECRET_KEY, previous=ENV_SECRET_KEY_PREVIOUS)


def secret_keys_dir(data_dir: Path) -> Path:
    return data_dir.joinpath(*SECRET_KEYS_PARTS)


def hub_key_provider(environ: Mapping[str, str], *, data_dir: Path) -> IRotatableKeyProvider:
    """The env source when ``BZ_HUB_SECRET_KEY`` is set, else the directory source —
    which mints its first generation when none exists."""
    return key_provider(
        environ, keys_dir=secret_keys_dir(data_dir), variables=HUB_KEY_VARIABLES, config_error=ConfigError
    )


def require_hub_key(environ: Mapping[str, str], *, data_dir: Path) -> None:
    """Refuse unless a hub key already exists — ``BZ_HUB_SECRET_KEY`` set or a generation on disk —
    without minting one, as :func:`hub_key_provider` would."""
    if key_exists(environ, keys_dir=secret_keys_dir(data_dir), variables=HUB_KEY_VARIABLES):
        return
    raise ConfigError(
        f"no hub key exists to seal secrets under — run `blizzard hub init` on this runtime or set {ENV_SECRET_KEY}"
    )


class StoreSecretReader:
    """Reveals a stored secret per use; refuses an unknown or retired name."""

    def __init__(self, *, catalog: ISecretCatalog, sealed: ISealedSecretRepository, cipher: ISecretCipher) -> None:
        self._catalog = catalog
        self._sealed = sealed
        self._cipher = cipher

    def reveal(self, name: SecretName) -> SecretValue:
        secret = self._sealed.get_sealed(name.value)
        if secret is None:
            raise SecretNotFound(name.value)
        require_revealable(name.value, retired=self._catalog.is_retired(name.value))
        return self._cipher.open(secret)


_store_secret_reader_conforms: type[ISecretReader] = StoreSecretReader


__all__ = [
    "ENV_SECRET_KEY",
    "ENV_SECRET_KEY_PREVIOUS",
    "HUB_KEY_VARIABLES",
    "StoreSecretReader",
    "hub_key_provider",
    "require_hub_key",
    "secret_keys_dir",
]

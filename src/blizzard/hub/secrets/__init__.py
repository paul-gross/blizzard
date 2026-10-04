"""The secret store's key source, cipher, reader, and startup key-coverage check.

The key is a process setting (``bzh:config-read-on-use`` Scope): ``BZ_HUB_SECRET_KEY``,
when set, is the only source; otherwise ``data/auth/secret-keys/`` holds the generations."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from blizzard.foundation.roles import domain_model
from blizzard.hub.config import ConfigError
from blizzard.hub.domain.secrets import (
    IHubKeyProvider,
    ISealedSecretRepository,
    ISecretCatalog,
    ISecretCipher,
    ISecretReader,
    SecretName,
    SecretNotFound,
    SecretRetired,
    SecretValue,
)
from blizzard.hub.secrets.internal.aes_gcm import AesGcmSecretCipher
from blizzard.hub.secrets.internal.directory_keys import DirectoryKeyProvider
from blizzard.hub.secrets.internal.env_keys import ENV_SECRET_KEY, ENV_SECRET_KEY_PREVIOUS, EnvKeyProvider


def secret_keys_dir(data_dir: Path) -> Path:
    return data_dir / "auth" / "secret-keys"


def hub_key_provider(environ: Mapping[str, str], *, data_dir: Path) -> IHubKeyProvider:
    """The env source when ``BZ_HUB_SECRET_KEY`` is set, else the directory source —
    which mints its first generation when none exists."""
    if environ.get(ENV_SECRET_KEY):
        return EnvKeyProvider(environ)
    if environ.get(ENV_SECRET_KEY_PREVIOUS):
        raise ConfigError(f"{ENV_SECRET_KEY_PREVIOUS} is set without {ENV_SECRET_KEY}")
    return DirectoryKeyProvider(secret_keys_dir(data_dir))


def secret_cipher(keys: IHubKeyProvider) -> ISecretCipher:
    return AesGcmSecretCipher(keys)


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
        if self._catalog.is_retired(name.value):
            raise SecretRetired(name.value)
        return self._cipher.open(secret)


def _conforms_store_secret_reader(x: StoreSecretReader) -> ISecretReader:
    return x


@domain_model
@dataclass(frozen=True)
class KeyCoverage:
    """Every key generation a stored row is sealed under that no provider generation answers."""

    missing: frozenset[str]

    @classmethod
    def of(cls, catalog: ISecretCatalog, keys: IHubKeyProvider) -> KeyCoverage:
        return cls(frozenset(catalog.key_ids_in_use() - keys.available_ids()))

    def check(self) -> None:
        """Refuse to start rather than serve secrets no generation can open."""
        if self.missing:
            raise ConfigError(
                f"stored secrets are sealed under key generation(s) {sorted(self.missing)} the hub key "
                f"source does not hold — restore the generation under data/auth/secret-keys/ or set "
                f"{ENV_SECRET_KEY}/{ENV_SECRET_KEY_PREVIOUS} to it"
            )


__all__ = [
    "ENV_SECRET_KEY",
    "ENV_SECRET_KEY_PREVIOUS",
    "KeyCoverage",
    "StoreSecretReader",
    "hub_key_provider",
    "secret_cipher",
    "secret_keys_dir",
]

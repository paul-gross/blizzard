"""Offline key rotation — re-seal every stored secret under the hub's current key.

Run on the hub host against the store and key source directly: the key is a process
setting on that host's filesystem, so no API route rotates it."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from blizzard.foundation.roles import domain_model
from blizzard.hub.config import ConfigError
from blizzard.hub.domain.config.secrets import (
    IHubKeyProvider,
    IResealSecretRepository,
    KeyGeneration,
    Reseal,
    SealedSecret,
    SealedValue,
    SecretUnreadable,
    stale_seals,
    uncovered_key_ids,
)
from blizzard.hub.secrets import ENV_SECRET_KEY, ENV_SECRET_KEY_PREVIOUS, secret_keys_dir
from blizzard.hub.secrets.internal.aes_gcm import AesGcmSecretCipher
from blizzard.hub.secrets.internal.directory_keys import DirectoryKeyProvider
from blizzard.hub.secrets.internal.env_keys import EnvKeyProvider


@domain_model
@dataclass(frozen=True)
class RotationResult:
    key_id: str
    resealed: int


class _Target:
    """The provider as it will read once the target generation is current: it seals under
    ``target`` and opens under whatever generation the source still holds."""

    def __init__(self, source: IHubKeyProvider, target: KeyGeneration) -> None:
        self._source = source
        self._target = target

    def current(self) -> KeyGeneration:
        return self._target

    def generation(self, key_id: str) -> KeyGeneration | None:
        return self._target if key_id == self._target.key_id else self._source.generation(key_id)

    def available_ids(self) -> frozenset[str]:
        return self._source.available_ids() | {self._target.key_id}


def rotate_keys(repo: IResealSecretRepository, environ: Mapping[str, str], *, data_dir: Path) -> RotationResult:
    """Re-seal every row not under the target generation, in one transaction.

    Env mode (``BZ_HUB_SECRET_KEY`` set) mints nothing; older rows open through
    ``BZ_HUB_SECRET_KEY_PREVIOUS``. Directory mode mints a generation, re-seals into it,
    then promotes it and prunes any generation file no row references."""
    if environ.get(ENV_SECRET_KEY):
        return _rotate(repo, EnvKeyProvider(environ), directory=None)
    if environ.get(ENV_SECRET_KEY_PREVIOUS):
        raise ConfigError(f"{ENV_SECRET_KEY_PREVIOUS} is set without {ENV_SECRET_KEY}")
    directory = DirectoryKeyProvider(secret_keys_dir(data_dir))
    return _rotate(repo, directory, directory=directory)


def _rotate(
    repo: IResealSecretRepository, keys: IHubKeyProvider, *, directory: DirectoryKeyProvider | None
) -> RotationResult:
    rows = repo.list_sealed()
    missing = uncovered_key_ids({row.sealed.key_id for row in rows}, keys.available_ids())
    if missing:
        raise ConfigError(
            f"stored secrets are sealed under key generation(s) {sorted(missing)} the key source does not hold — "
            f"restore them (set {ENV_SECRET_KEY_PREVIOUS} in env mode) before rotating"
        )
    target = directory.mint() if directory is not None else keys.current()
    cipher = AesGcmSecretCipher(_Target(keys, target))
    changes = [_reseal(cipher, row) for row in stale_seals(rows, target.key_id)]
    repo.reseal(changes)
    if directory is not None:
        directory.promote(target.key_id)
        directory.prune(frozenset(row.sealed.key_id for row in repo.list_sealed()))
    return RotationResult(key_id=target.key_id, resealed=len(changes))


def _reseal(cipher: AesGcmSecretCipher, row: SealedSecret) -> Reseal:
    try:
        value = cipher.open(row)
    except SecretUnreadable as exc:
        raise ConfigError(str(exc)) from exc
    sealed: SealedValue = cipher.seal(value, name=row.name, revision=row.revision)
    return Reseal(name=row.name, revision=row.revision, from_key_id=row.sealed.key_id, sealed=sealed)

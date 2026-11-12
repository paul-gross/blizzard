"""Builders for the key-source and cipher adapters under ``internal/`` — a daemon's composition reaches them here
(``bzh:internal-visibility``)."""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path

from blizzard.foundation.secrets.internal.aes_gcm import AesGcmSecretCipher
from blizzard.foundation.secrets.internal.directory_keys import DirectoryKeyProvider
from blizzard.foundation.secrets.internal.env_keys import EnvKeyProvider
from blizzard.foundation.secrets.key_variables import KeyVariables
from blizzard.foundation.secrets.sealing import IKeyProvider, IRotatableKeyProvider, ISecretCipher


def secret_cipher(keys: IKeyProvider) -> ISecretCipher:
    return AesGcmSecretCipher(keys)


def key_provider(
    environ: Mapping[str, str],
    *,
    keys_dir: Path,
    variables: KeyVariables,
    config_error: type[Exception],
) -> IRotatableKeyProvider:
    """The env source when ``variables.current`` is set, else the directory source — which mints its first
    generation when none exists. ``config_error`` is the daemon's own refusal type."""
    if environ.get(variables.current):
        return EnvKeyProvider(environ, variables=variables, config_error=config_error)
    if environ.get(variables.previous):
        raise config_error(f"{variables.previous} is set without {variables.current}")
    return DirectoryKeyProvider(keys_dir, config_error=config_error)


def key_exists(environ: Mapping[str, str], *, keys_dir: Path, variables: KeyVariables) -> bool:
    """Whether a key already exists — the current variable set or a generation on disk — without minting one, as
    :func:`key_provider` would."""
    return bool(environ.get(variables.current)) or DirectoryKeyProvider.initialized(keys_dir)

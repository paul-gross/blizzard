"""The hub's binding of offline key rotation — its refusal type and its variable names over the shared algorithm."""

from __future__ import annotations

from collections.abc import Callable, Sequence

from blizzard.foundation.secrets.rotation import RotationResult
from blizzard.foundation.secrets.rotation import rotate_keys as rotate_keys_with
from blizzard.foundation.secrets.sealing import (
    IKeyProvider,
    IResealSecretRepository,
    IRotatableKeyProvider,
    ISecretCipher,
)
from blizzard.hub.config import ConfigError
from blizzard.hub.secrets import HUB_KEY_VARIABLES


def rotate_keys(
    repos: Sequence[IResealSecretRepository],
    keys: IRotatableKeyProvider,
    cipher_for: Callable[[IKeyProvider], ISecretCipher],
) -> RotationResult:
    """Re-seal every open tenant's stored secrets under the hub's current key — one rotation covers every tenant."""
    return rotate_keys_with(repos, keys, cipher_for, variables=HUB_KEY_VARIABLES, config_error=ConfigError)

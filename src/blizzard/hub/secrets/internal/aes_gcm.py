"""The AES-256-GCM :class:`ISecretCipher` binding."""

from __future__ import annotations

import json
import secrets

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from blizzard.hub.domain.config.secrets import (
    IHubKeyProvider,
    ISecretCipher,
    SealedSecret,
    SealedValue,
    SecretUnreadable,
    SecretValue,
)

_NONCE_BYTES = 12
_AD_VERSION = 1


def associated_data(*, name: str, revision: int) -> bytes:
    """Versioned, delimited JSON — never a bare concatenation, so no two
    ``(name, revision)`` pairs encode alike and a tenant id can join later."""
    return json.dumps(
        {"v": _AD_VERSION, "name": name, "revision": revision}, sort_keys=True, separators=(",", ":")
    ).encode()


class AesGcmSecretCipher:
    """Seals under the provider's current generation; opens under the row's own ``key_id``."""

    def __init__(self, keys: IHubKeyProvider) -> None:
        self._keys = keys

    def seal(self, value: SecretValue, *, name: str, revision: int) -> SealedValue:
        generation = self._keys.current()
        nonce = secrets.token_bytes(_NONCE_BYTES)
        ciphertext = AESGCM(generation.material).encrypt(
            nonce, value.expose().encode(), associated_data(name=name, revision=revision)
        )
        return SealedValue(key_id=generation.key_id, ciphertext=ciphertext, nonce=nonce)

    def open(self, secret: SealedSecret) -> SecretValue:
        generation = self._keys.generation(secret.sealed.key_id)
        if generation is None:
            raise SecretUnreadable(secret.name, key_id=secret.sealed.key_id)
        try:
            plaintext = AESGCM(generation.material).decrypt(
                secret.sealed.nonce,
                secret.sealed.ciphertext,
                associated_data(name=secret.name, revision=secret.revision),
            )
        except InvalidTag:
            raise SecretUnreadable(secret.name, key_id=secret.sealed.key_id) from None
        return SecretValue(plaintext.decode())


def _conforms_aes_gcm_secret_cipher(x: AesGcmSecretCipher) -> ISecretCipher:
    return x

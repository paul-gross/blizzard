"""The AES-256-GCM secret cipher — sealing bound to ``(name, revision)`` (unit tier)."""

from __future__ import annotations

import pytest

from blizzard.hub.domain.config.secrets import KeyGeneration, SealedSecret, SecretUnreadable, SecretValue
from blizzard.hub.secrets.internal.aes_gcm import AesGcmSecretCipher, associated_data
from blizzard.hub.secrets.internal.key_material import generation_of

pytestmark = pytest.mark.unit


class _Keys:
    def __init__(self, *generations: KeyGeneration) -> None:
        self._current = generations[0]
        self._by_id = {g.key_id: g for g in generations}

    def current(self) -> KeyGeneration:
        return self._current

    def generation(self, key_id: str) -> KeyGeneration | None:
        return self._by_id.get(key_id)

    def available_ids(self) -> frozenset[str]:
        return frozenset(self._by_id)


_KEY = generation_of(bytes(range(32)))
_OTHER_KEY = generation_of(bytes(range(1, 33)))


def test_a_sealed_value_opens_at_its_own_name_and_revision() -> None:
    cipher = AesGcmSecretCipher(_Keys(_KEY))
    sealed = cipher.seal(SecretValue("tok-1"), name="gh", revision=3)

    assert sealed.key_id == _KEY.key_id
    assert b"tok-1" not in sealed.ciphertext
    assert cipher.open(SealedSecret(name="gh", revision=3, sealed=sealed)).expose() == "tok-1"


def test_a_ciphertext_copied_onto_another_name_fails_to_open() -> None:
    cipher = AesGcmSecretCipher(_Keys(_KEY))
    sealed = cipher.seal(SecretValue("tok-1"), name="gh", revision=1)

    with pytest.raises(SecretUnreadable):
        cipher.open(SealedSecret(name="other", revision=1, sealed=sealed))


def test_a_ciphertext_replayed_at_another_revision_fails_to_open() -> None:
    cipher = AesGcmSecretCipher(_Keys(_KEY))
    sealed = cipher.seal(SecretValue("tok-1"), name="gh", revision=1)

    with pytest.raises(SecretUnreadable):
        cipher.open(SealedSecret(name="gh", revision=2, sealed=sealed))


def test_every_seal_draws_a_fresh_nonce() -> None:
    cipher = AesGcmSecretCipher(_Keys(_KEY))
    first = cipher.seal(SecretValue("tok-1"), name="gh", revision=1)
    second = cipher.seal(SecretValue("tok-1"), name="gh", revision=1)

    assert len(first.nonce) == 12
    assert first.nonce != second.nonce
    assert first.ciphertext != second.ciphertext


def test_a_row_opens_under_its_own_generation_not_the_current_one() -> None:
    sealed = AesGcmSecretCipher(_Keys(_OTHER_KEY)).seal(SecretValue("tok-1"), name="gh", revision=1)
    cipher = AesGcmSecretCipher(_Keys(_KEY, _OTHER_KEY))

    assert cipher.open(SealedSecret(name="gh", revision=1, sealed=sealed)).expose() == "tok-1"


def test_a_missing_generation_names_it() -> None:
    sealed = AesGcmSecretCipher(_Keys(_OTHER_KEY)).seal(SecretValue("tok-1"), name="gh", revision=1)

    with pytest.raises(SecretUnreadable) as caught:
        AesGcmSecretCipher(_Keys(_KEY)).open(SealedSecret(name="gh", revision=1, sealed=sealed))
    assert caught.value.key_id == _OTHER_KEY.key_id


def test_associated_data_never_collides_across_name_and_revision_splits() -> None:
    assert associated_data(name="a1", revision=2) != associated_data(name="a", revision=12)


def test_a_secret_value_and_a_key_generation_are_redacted() -> None:
    value = SecretValue("tok-planted")

    assert "tok-planted" not in repr(value)
    assert "tok-planted" not in str(value)
    assert f"{value}" == str(value)
    assert repr(bytes(range(32))) not in repr(_KEY)

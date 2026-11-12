"""Offline key rotation — re-seal every stored secret under the key source's target generation.

Run on the daemon's host against the store and key source directly: the key is a process
setting on that host's filesystem, so no API route rotates it. The key is the daemon's, not
a tenant's: one rotation covers every open tenant."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass

from blizzard.foundation.roles import domain_model
from blizzard.foundation.secrets.key_variables import KeyVariables
from blizzard.foundation.secrets.sealing import (
    IKeyProvider,
    IResealSecretRepository,
    IRotatableKeyProvider,
    ISecretCipher,
    KeyGeneration,
    Reseal,
    SealedSecret,
    SealedValue,
    SecretUnreadable,
    stale_seals,
    uncovered_key_ids,
)


@domain_model
@dataclass(frozen=True)
class RotationResult:
    key_id: str
    resealed: int


class _Target:
    """The provider as it will read once the target generation is current: it seals under
    ``target`` and opens under whatever generation the source still holds."""

    def __init__(self, source: IKeyProvider, target: KeyGeneration) -> None:
        self._source = source
        self._target = target

    def current(self) -> KeyGeneration:
        return self._target

    def generation(self, key_id: str) -> KeyGeneration | None:
        return self._target if key_id == self._target.key_id else self._source.generation(key_id)

    def available_ids(self) -> frozenset[str]:
        return self._source.available_ids() | {self._target.key_id}


def rotate_keys(
    repos: Sequence[IResealSecretRepository],
    keys: IRotatableKeyProvider,
    cipher_for: Callable[[IKeyProvider], ISecretCipher],
    *,
    variables: KeyVariables,
    config_error: type[Exception],
) -> RotationResult:
    """Re-seal every row not under the target generation — one transaction per tenant, one
    ``repos`` member per open tenant.

    ``keys`` mints the target generation (the environment source's: see
    `src/blizzard/foundation/secrets/internal/env_keys.py`). Only once every tenant has been re-sealed does it
    promote that generation and prune any generation no row references. A tenant that fails leaves the
    old generation current: the tenants before it open through the minted generation, and a re-run
    moves every tenant again. ``cipher_for`` builds the cipher over
    the key view rotation seals and opens through. Refusals raise ``config_error``, and name
    ``variables.previous`` as the variable that restores a missing generation."""
    rows = [repo.list_sealed() for repo in repos]
    missing = uncovered_key_ids(_key_ids(rows), keys.available_ids())
    if missing:
        raise config_error(
            f"stored secrets are sealed under key generation(s) {sorted(missing)} the key source does not hold — "
            f"restore them (set {variables.previous} in env mode) before rotating"
        )
    target = keys.mint()
    cipher = cipher_for(_Target(keys, target))
    resealed = 0
    for repo, tenant_rows in zip(repos, rows, strict=True):
        changes = [_reseal(cipher, row, config_error) for row in stale_seals(tenant_rows, target.key_id)]
        repo.reseal(changes)
        resealed += len(changes)
    keys.promote(target.key_id)
    keys.prune(_key_ids([repo.list_sealed() for repo in repos]))
    return RotationResult(key_id=target.key_id, resealed=resealed)


def _key_ids(rows: Sequence[Sequence[SealedSecret]]) -> frozenset[str]:
    return frozenset(row.sealed.key_id for tenant_rows in rows for row in tenant_rows)


def _reseal(cipher: ISecretCipher, row: SealedSecret, config_error: type[Exception]) -> Reseal:
    try:
        value = cipher.open(row)
    except SecretUnreadable as exc:
        raise config_error(str(exc)) from exc
    sealed: SealedValue = cipher.seal(value, name=row.name, revision=row.revision)
    return Reseal(name=row.name, revision=row.revision, from_key_id=row.sealed.key_id, sealed=sealed)

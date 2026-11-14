"""What a repository or work source naming a credential holds of it — its id and keyed lock — and the refusals such a
record earns for the credential it names. It imports neither the credential model nor the referrer records, so both
sides import it."""

from __future__ import annotations

from typing import NewType

from blizzard.hub.domain.config.errors import ConfigFieldError
from blizzard.hub.domain.kernel.locks import LockKey, LockNamespace
from blizzard.hub.domain.kernel.unset import UNSET, UnsetType

CredentialId = NewType("CredentialId", str)

#: The reference field a repository or work-source write names its credential by.
CREDENTIAL_FIELD = "credential"


def credential_lock(credential_id: CredentialId) -> LockKey:
    """The keyed lock on ``credential_id``'s credential (``bzh:transaction-graduation``): a unit writing the
    credential, or a record naming it, declares it."""
    return LockKey(LockNamespace.CREDENTIAL, credential_id)


class CredentialUnavailable(ConfigFieldError):
    """The credential a write names is unknown, or retired while the record written is enabled — enable it rather
    than have a write create another."""

    code = "credential_unavailable"

    def __init__(self, credential: str, *, retired: bool, field: str = CREDENTIAL_FIELD) -> None:
        """``field`` is the one the write named the credential by — ``credential``, or an old secret field it resolved
        from."""
        super().__init__(field, f"credential {credential} is {'retired' if retired else 'unknown'}")
        self.credential = credential
        self.retired = retired


class CredentialForgeMismatch(ConfigFieldError):
    """The credential a record names is of another forge than the record's — or an edit moves a record naming a
    credential to another forge without naming one there."""

    code = "credential_forge_mismatch"


class CredentialBindingUnsupported(ConfigFieldError):
    """A work source's provider cannot act with the credential's binding."""

    code = "credential_binding_unsupported"


class CredentialConflict(ConfigFieldError):
    """An old secret field disagrees with the credential the record names: sent beside ``credential`` and not its
    ``github.token`` secret, or changed on a record naming a credential without naming one."""

    code = "credential_conflict"


def require_link_kept(
    credential: CredentialId | None,
    named: CredentialId | None | UnsetType,
    forge: tuple[str, str],
    secret: tuple[str | None, str | None],
    *,
    secret_field: str,
) -> None:
    """Refuse an edit of a record naming ``credential`` that names no credential itself (``named`` is
    :data:`UNSET`) yet moves the record's forge — :class:`CredentialForgeMismatch` — or its old ``secret_field`` —
    :class:`CredentialConflict`: either would leave the record on a credential of another forge or secret. Each pair
    is the stored value and the edited one."""
    if credential is None or named is not UNSET:
        return
    if forge[0] != forge[1]:
        raise CredentialForgeMismatch(
            "forge",
            f"the record acts with credential {credential} of forge {forge[0]}; name a credential on {forge[1]}",
        )
    if secret[0] != secret[1]:
        raise CredentialConflict(
            secret_field, f"the record acts with credential {credential}; name a credential instead of a secret"
        )

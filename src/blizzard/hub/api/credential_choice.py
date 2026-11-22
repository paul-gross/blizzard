"""What a repository or work-source write states of its credential, as the domain reads it
(``bzh:domain-takes-objects``)."""

from __future__ import annotations

from typing import Protocol

from blizzard.hub.domain.config.credential_naming import CREDENTIAL_FIELD
from blizzard.hub.domain.config.credential_resolution import CredentialChoice
from blizzard.hub.domain.config.credentials import IReadCredentialRepository


class _NamesCredential(Protocol):
    @property
    def credential(self) -> str | None: ...

    @property
    def model_fields_set(self) -> set[str]: ...


def credential_choice(request: _NamesCredential, credentials: IReadCredentialRepository) -> CredentialChoice:
    """``request``'s ``credential`` with the credential it resolves to: no choice when it left the field out, and a
    clear — ``None`` — when it sent an explicit ``null``."""
    if CREDENTIAL_FIELD not in request.model_fields_set:
        return CredentialChoice()
    if request.credential is None:
        return CredentialChoice(credential=None)
    return CredentialChoice(credential=request.credential, named=credentials.resolve(request.credential))

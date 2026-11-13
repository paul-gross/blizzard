"""What a repository or work-source write states of its credential, as the domain reads it
(``bzh:domain-takes-objects``)."""

from __future__ import annotations

from typing import Protocol

from blizzard.hub.composition import TenantServices
from blizzard.hub.domain.config.credential_naming import CREDENTIAL_FIELD
from blizzard.hub.domain.config.credential_resolution import CredentialChoice


class _NamesCredential(Protocol):
    @property
    def credential(self) -> str | None: ...

    @property
    def model_fields_set(self) -> set[str]: ...


def credential_choice(request: _NamesCredential, services: TenantServices) -> CredentialChoice:
    """``request``'s ``credential`` with the credential it resolves to — the choice of none when it sent none."""
    if request.credential is None or CREDENTIAL_FIELD not in request.model_fields_set:
        return CredentialChoice()
    return CredentialChoice(
        credential=request.credential, named=services.credential_records.resolve(request.credential)
    )

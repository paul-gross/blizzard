"""The forge authorization boundary — the one place a forge credential becomes what a client authenticates with.

Landing and ingestion each ask :class:`IForgeAuthorizations` for the authorization of the credential a record acts
with, and authenticate their clients through its ``auth``; neither reveals a secret. The authorization's
``authority`` keys the rate budget and the write lane every caller acting as that authority shares."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol

import httpx2

from blizzard.hub.domain.config.capabilities import Capability
from blizzard.hub.domain.config.credentials import CredentialId, ResolvedCredential


class ForgeScope(StrEnum):
    """What an authorization is asked for: landing a change, or reading and writing a work source's items."""

    LANDING = "landing"
    WORK_SOURCE = "work-source"


@dataclass(frozen=True)
class ForgeAuthorization:
    """One credential's authorization: who it is (``credential_id`` and ``credential_slug`` are ``None`` for a record
    naming no credential), the binding and effective ``variant`` it was built as, the ``authority`` it acts as, what it
    can do on its forge, and the ``auth`` that authenticates each request — one object serving sync and async clients.
    """

    credential_id: CredentialId | None
    credential_slug: str | None
    binding: str
    variant: str | None
    authority: str
    offered: frozenset[Capability]
    auth: httpx2.Auth


class IForgeAuthorizations(Protocol):
    """Builds a credential's authorization, revealing its secret — the only forge code that does."""

    def authorization(self, credential: ResolvedCredential, scope: ForgeScope) -> ForgeAuthorization:
        """``credential``'s authorization for ``scope``; :class:`~blizzard.hub.domain.config.credentials.
        CredentialUnavailable` when the credential is retired or its secret is unknown or retired."""
        ...


@dataclass(frozen=True)
class ScopedAuthorizations:
    """``authorizations`` fixed to one ``scope``, as a caller that only ever asks for that scope is handed them."""

    authorizations: IForgeAuthorizations
    scope: ForgeScope

    def authorization(self, credential: ResolvedCredential) -> ForgeAuthorization:
        return self.authorizations.authorization(credential, self.scope)

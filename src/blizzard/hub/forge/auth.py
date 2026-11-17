"""The forge authorization boundary — the one place a forge credential becomes what a client authenticates with.

Landing and ingestion each ask :class:`IForgeAuthorizations` for the authorization of the credential a record acts
with, and authenticate their clients through its ``auth``; neither reveals a secret. The authorization's
``authority`` keys the rate budget and the write lane every caller acting as that authority shares."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol

import httpx2

from blizzard.hub.domain.config.capabilities import Capability
from blizzard.hub.domain.config.credential_naming import CredentialId
from blizzard.hub.domain.config.credentials import ResolvedCredential


class ForgeScope(StrEnum):
    """What an authorization is asked for: landing a change, reading and writing a work source's items, or checking
    read-only what the credential can do."""

    LANDING = "landing"
    WORK_SOURCE = "work-source"
    CHECK = "check"


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
    #: Whether its access reaches only the repositories a grant includes.
    grant_scoped: bool = False
    #: Whether the forge has since narrowed what it offers, so its holder builds it again.
    outdated: Callable[[], bool] = lambda: False
    #: The permissions an installation grants, as the build read them; ``None`` where the build read none.
    granted_permissions: Mapping[str, str] | None = None
    #: Whether a declared variant disagrees with the form of the token it was declared for.
    variant_contradicted: bool = False


class IForgeAuthorizations(Protocol):
    """Builds a credential's authorization, revealing its secret — the only forge code that does."""

    def authorization(
        self, credential: ResolvedCredential, scope: ForgeScope, *, call_timeout: Callable[[], float] | None = None
    ) -> ForgeAuthorization:
        """``credential``'s authorization for ``scope``, each call building or minting it bounded by ``call_timeout``'s
        seconds when given; :class:`~blizzard.hub.domain.config.credentials.
        CredentialUnavailable` when the credential is retired or its secret is unknown or retired."""
        ...


@dataclass(frozen=True)
class ScopedAuthorizations:
    """``authorizations`` fixed to one ``scope``, as a caller that only ever asks for that scope is handed them."""

    authorizations: IForgeAuthorizations
    scope: ForgeScope

    def authorization(self, credential: ResolvedCredential) -> ForgeAuthorization:
        return self.authorizations.authorization(credential, self.scope)

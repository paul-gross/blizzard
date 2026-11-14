"""The ``github.token`` binding's authorization: the token's effective variant, what that leaves the forge's kind
offering, and the ``httpx2.Auth`` that sends the token on every request.

The variant is read from the revealed value at build; the value, and any part of it, never leaves this module but on
the ``Authorization`` header — only the variant is logged or returned."""

from __future__ import annotations

import re
from collections.abc import Generator
from enum import StrEnum

import httpx2

from blizzard.foundation.secrets.value import SecretValue
from blizzard.hub.domain.config.capabilities import Capability
from blizzard.hub.domain.config.credential_bindings import FINE_GRAINED_WITHHELD
from blizzard.hub.domain.config.credentials import ResolvedCredential
from blizzard.hub.forge.auth import ForgeAuthorization

_FINE_GRAINED_PREFIX = "github_pat_"
_CLASSIC_PREFIX = "ghp_"
#: A classic token of the form GitHub issued before prefixes.
_LEGACY_CLASSIC = re.compile(r"[0-9a-f]{40}")


class TokenVariant(StrEnum):
    """A GitHub token's effective variant: what the hub treats it as, from its declared variant and its value."""

    CLASSIC = "classic"
    FINE_GRAINED = "fine-grained"
    UNSPECIFIED = "unspecified"


def effective_variant(declared: object, token: str) -> TokenVariant:
    """The variant a token declared as ``declared`` (``None`` when undeclared) acts as: fine-grained by its prefix or
    its declaration, classic by its form unless declared fine-grained, else as declared — unspecified when not."""
    if token.startswith(_FINE_GRAINED_PREFIX) or declared == TokenVariant.FINE_GRAINED:
        return TokenVariant.FINE_GRAINED
    if token.startswith(_CLASSIC_PREFIX) or _LEGACY_CLASSIC.fullmatch(token) or declared == TokenVariant.CLASSIC:
        return TokenVariant.CLASSIC
    return TokenVariant.UNSPECIFIED


def token_offers(variant: TokenVariant, kind: frozenset[Capability]) -> frozenset[Capability]:
    """What a token of ``variant`` can do on a forge whose kind offers ``kind``."""
    if variant is TokenVariant.FINE_GRAINED:
        return kind - FINE_GRAINED_WITHHELD
    return kind


class GitHubTokenAuth(httpx2.Auth):
    """Sends ``token`` as ``Authorization: token …`` on each request, through a sync or an async client alike."""

    def __init__(self, token: SecretValue) -> None:
        self._token = token

    def auth_flow(self, request: httpx2.Request) -> Generator[httpx2.Request, httpx2.Response, None]:
        request.headers["Authorization"] = f"token {self._token.expose()}"
        yield request


def github_token_authorization(
    credential: ResolvedCredential, token: SecretValue, kind: frozenset[Capability]
) -> ForgeAuthorization:
    """``credential``'s authorization over its revealed ``token``, on a forge whose kind offers ``kind``."""
    variant = effective_variant(credential.settings.get("variant"), token.expose())
    return ForgeAuthorization(
        credential_id=credential.credential_id,
        credential_slug=credential.slug,
        binding=credential.binding,
        variant=variant.value,
        authority=credential.authority,
        offered=token_offers(variant, kind),
        auth=GitHubTokenAuth(token),
    )

"""Forge authorizations for tests: one built over a plain token, the header and token an auth sends, and an
authorization boundary made to refuse chosen secrets as retired."""

from __future__ import annotations

from collections.abc import Callable

import httpx2
import pytest

from blizzard.foundation.secrets.value import SecretValue
from blizzard.hub.domain.config.credentials import CredentialUnavailable, ResolvedCredential
from blizzard.hub.domain.config.forges import FORGE_KIND_CAPABILITIES, ForgeId
from blizzard.hub.forge.auth import ForgeAuthorization, ForgeScope, IForgeAuthorizations
from blizzard.hub.forge.internal.github_auth import github_token_authorization


def token_authorization(token: str = "tok", *, secret_name: str = "gh") -> ForgeAuthorization:
    """The authorization a repository naming no credential acts with over secret ``secret_name`` holding ``token``."""
    credential = ResolvedCredential(
        credential_id=None,
        forge_id=ForgeId("frg_github"),
        binding="github.token",
        secret_name=secret_name,
        settings={},
        revision=None,
        authority=f"secret/{secret_name}",
    )
    return github_token_authorization(credential, SecretValue(token), FORGE_KIND_CAPABILITIES["github"])


def sent_header(auth: httpx2.Auth | None) -> str | None:
    """The ``Authorization`` header ``auth`` puts on a request."""
    assert auth is not None, "the client authenticates no request"
    request = httpx2.Request("GET", "http://forge.test/")
    next(auth.sync_auth_flow(request))
    return request.headers.get("Authorization")


def sent_authorization(authorization: ForgeAuthorization) -> str | None:
    """The ``Authorization`` header ``authorization`` puts on a request."""
    return sent_header(authorization.auth)


def sent_token(authorization: ForgeAuthorization) -> str:
    """The token ``authorization`` sends."""
    header = sent_authorization(authorization)
    assert header is not None and header.startswith("token ")
    return header.removeprefix("token ")


def retire_secrets(
    monkeypatch: pytest.MonkeyPatch,
    authorizations: IForgeAuthorizations,
    retired: Callable[[str], bool],
    asked: list[str] | None = None,
) -> None:
    """Make ``authorizations`` refuse every secret ``retired`` names as retired, noting each secret asked for in
    ``asked``."""
    authorize = authorizations.authorization

    def authorization(credential: ResolvedCredential, scope: ForgeScope) -> ForgeAuthorization:
        if asked is not None:
            asked.append(credential.secret_name)
        if retired(credential.secret_name):
            raise CredentialUnavailable(f"secret {credential.secret_name}", retired=True)
        return authorize(credential, scope)

    monkeypatch.setattr(authorizations, "authorization", authorization)

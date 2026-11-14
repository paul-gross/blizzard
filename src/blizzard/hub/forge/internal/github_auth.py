"""GitHub's credential bindings' authorizations.

``github.token``: the token's effective variant, what that leaves the forge's kind offering, and the ``httpx2.Auth``
that sends the token on every request. The variant is read from the revealed value at build; the value, and any part
of it, never leaves this module but on the ``Authorization`` header — only the variant is logged or returned.

``github.app-installation``: installation tokens minted with the App's private key, per tenant, held in memory only,
renewed before they expire and dropped when refused. The key lives only inside the built auth; the JWT and every
token leave only on an ``Authorization`` header."""

from __future__ import annotations

import asyncio
import re

# Guards this process's own token map and serializes its mints, never a store write.
# ast-grep-ignore: bzh:store-exclusive-write
import threading
from collections.abc import AsyncGenerator, Generator, Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import StrEnum
from typing import cast

import httpx2
import jwt

from blizzard.foundation.clock import IClock
from blizzard.foundation.logging import get_logger
from blizzard.foundation.roles import adapter_model
from blizzard.foundation.secrets.value import SecretValue
from blizzard.foundation.untyped import as_table
from blizzard.hub.domain.config.capabilities import Capability
from blizzard.hub.domain.config.credential_bindings import FINE_GRAINED_WITHHELD
from blizzard.hub.domain.config.credentials import ResolvedCredential
from blizzard.hub.domain.config.secrets import RevealedSecret
from blizzard.hub.domain.kernel.forge_refusals import (
    AuthRefusal,
    ClassifiedRefusal,
    ForgeDiagnostic,
    ForgeMintRefused,
)
from blizzard.hub.forge.auth import ForgeAuthorization, ForgeScope
from blizzard.hub.forge.internal.github_refusals import CallCredential, classify_mint_refusal, diagnostic_of

_log = get_logger("blizzard.hub.forge.github_auth")

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


def variant_withholds(variant: str | None) -> frozenset[Capability]:
    """What a credential of effective ``variant`` cannot do whatever its forge's kind offers — empty for any
    credential that is no fine-grained token."""
    return FINE_GRAINED_WITHHELD if variant == TokenVariant.FINE_GRAINED else frozenset()


def token_offers(variant: TokenVariant, kind: frozenset[Capability]) -> frozenset[Capability]:
    """What a token of ``variant`` can do on a forge whose kind offers ``kind``."""
    return kind - variant_withholds(variant)


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
    """``credential``'s authorization over its revealed ``token``, on a forge whose kind offers ``kind`` — every
    scope's is the same token."""
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


# --- github.app-installation ----------------------------------------------------------------------------------------

#: How long before its ``expires_at`` an installation token is renewed rather than sent.
RENEW_BEFORE = timedelta(minutes=5)
#: The App JWT's backdated issue instant, absorbing clock drift between the hub and the forge.
_JWT_BACKDATE = timedelta(seconds=60)
#: The App JWT's lifetime from now — inside GitHub's ten-minute bound.
_JWT_LIFETIME = timedelta(seconds=540)
#: Bounds one App-authenticated call: the installation read or a mint.
APP_CALL_TIMEOUT_SECONDS = 30.0

#: The permissions every installation token of a scope is minted with: what that scope's calls need.
APP_REQUIRED_PERMISSIONS: Mapping[ForgeScope, Mapping[str, str]] = {
    ForgeScope.LANDING: {"metadata": "read", "contents": "write", "pull_requests": "write"},
    ForgeScope.WORK_SOURCE: {"metadata": "read", "issues": "write", "pull_requests": "read"},
}
#: The permissions a scope's token is minted with only when the installation grants them, and the capability its
#: authorization withholds when it does not. Reading a revision's checks reads its check runs and its commit statuses,
#: so ``checks.read`` is withheld unless both are granted.
APP_OPTIONAL_PERMISSIONS: Mapping[ForgeScope, Mapping[str, tuple[str, Capability]]] = {
    ForgeScope.LANDING: {
        "checks": ("read", Capability.CHECKS_READ),
        "statuses": ("read", Capability.CHECKS_READ),
        "actions": ("write", Capability.CHECKS_RERUN),
    },
    ForgeScope.WORK_SOURCE: {},
}

#: GitHub's permission levels, weakest first; a grant covers every level up to its own.
_LEVELS = ("read", "write", "admin")

_INSTALLATION = "GET /app/installations/{installation_id}"
_MINT = "POST /app/installations/{installation_id}/access_tokens"


def covers(granted: str | None, needed: str) -> bool:
    """Whether a grant at ``granted`` (``None`` when not granted) covers the level ``needed``."""
    return granted in _LEVELS and _LEVELS.index(granted) >= _LEVELS.index(needed)


def app_permissions(scope: ForgeScope, granted: Mapping[str, str]) -> dict[str, str]:
    """The permissions a ``scope`` token is minted with on an installation granting ``granted``: the scope's required
    permissions, and each optional one the installation grants."""
    optional = {
        permission: level
        for permission, (level, _capability) in APP_OPTIONAL_PERMISSIONS[scope].items()
        if covers(granted.get(permission), level)
    }
    return {**APP_REQUIRED_PERMISSIONS[scope], **optional}


def app_offers(scope: ForgeScope, granted: Mapping[str, str], kind: frozenset[Capability]) -> frozenset[Capability]:
    """What a ``scope`` authorization on an installation granting ``granted`` can do on a forge whose kind offers
    ``kind``: the kind's set less each capability whose optional permission is not granted."""
    withheld = {
        capability
        for permission, (level, capability) in APP_OPTIONAL_PERMISSIONS[scope].items()
        if not covers(granted.get(permission), level)
    }
    return kind - withheld


def _missing_permissions(scope: ForgeScope, granted: Mapping[str, str]) -> dict[str, str]:
    return {
        permission: level
        for permission, level in APP_REQUIRED_PERMISSIONS[scope].items()
        if not covers(granted.get(permission), level)
    }


def _permission_list(permissions: Mapping[str, str]) -> str:
    return ", ".join(f"{permission}={level}" for permission, level in sorted(permissions.items()))


@adapter_model
@dataclass(frozen=True)
class InstallationKey:
    """What one installation token is held under: the forge, the installation's authority, the scope it was minted
    for, and the revision of the App key that signed its mint — a replaced key mints under a new key."""

    forge_id: str
    authority: str
    scope: ForgeScope
    secret_revision: int


@adapter_model
@dataclass(frozen=True)
class MintedToken:
    """An installation token, when it expires, and the permissions GitHub minted it with."""

    token: SecretValue
    expires_at: datetime
    permissions: Mapping[str, str]


@adapter_model
@dataclass(frozen=True)
class AppInstallation:
    """What a mint is signed and addressed with: the forge's API base, the App and its installation, the App's private
    key, and the credential a refusal names."""

    api_base: str
    app_id: int
    installation_id: int
    private_key: SecretValue
    credential: CallCredential


@adapter_model
@dataclass(frozen=True)
class Installation:
    """An installation as the App reads it: the permissions it grants."""

    permissions: Mapping[str, str]


def app_jwt(app_id: int, private_key: SecretValue, now: datetime) -> str:
    """The App's JWT at ``now``: RS256 over ``iss``, a backdated ``iat`` and an ``exp`` inside GitHub's bound."""
    claims = {
        "iss": str(app_id),
        "iat": int((now - _JWT_BACKDATE).timestamp()),
        "exp": int((now + _JWT_LIFETIME).timestamp()),
    }
    return jwt.encode(claims, private_key.expose(), algorithm="RS256")


class InstallationTokens:
    """One tenant's installation tokens, held in memory only: each is minted on first use, renewed once within
    :data:`RENEW_BEFORE` of expiring, and dropped when a request it carried was refused. One mint at a time per key;
    a waiter takes the token the mint it waited on made. An installation's grant is read once per App key revision.
    Holds no key: each call is handed the :class:`AppInstallation` it signs with."""

    def __init__(self, *, clock: IClock, transport: httpx2.BaseTransport | None = None) -> None:
        self._clock = clock
        self._transport = transport
        self._guard = threading.Lock()
        self._locks: dict[InstallationKey, threading.Lock] = {}
        self._tokens: dict[InstallationKey, MintedToken] = {}
        self._installations: dict[tuple[str, str, int], Installation] = {}

    def installation(self, key: InstallationKey, app: AppInstallation) -> Installation:
        """The installation ``app`` names, read once per ``key``'s forge, authority and key revision;
        :class:`ForgeMintRefused` when GitHub refuses the App or the installation is missing or suspended."""
        held = (key.forge_id, key.authority, key.secret_revision)
        with self._guard:
            known = self._installations.get(held)
        if known is not None:
            return known
        response = self._call(app, "GET", f"/app/installations/{app.installation_id}")
        payload = self._answer(response, app, "read_installation", _INSTALLATION)
        if payload.get("suspended_at"):
            raise ForgeMintRefused(
                ClassifiedRefusal(
                    AuthRefusal.INSTALLATION_SUSPENDED,
                    diagnostic_of(
                        response.status_code,
                        response.headers,
                        "the installation is suspended",
                        documentation_url=None,
                        operation="read_installation",
                        endpoint=_INSTALLATION,
                        repository=None,
                        credential=app.credential,
                    ),
                )
            )
        granted = as_table(payload.get("permissions")) or {}
        installation = Installation({str(name): str(level) for name, level in granted.items()})
        with self._guard:
            self._installations[held] = installation
        return installation

    def fresh(self, key: InstallationKey) -> MintedToken | None:
        """``key``'s held token while more than :data:`RENEW_BEFORE` is left on it — never any I/O."""
        with self._guard:
            token = self._tokens.get(key)
        if token is None or token.expires_at - self._clock.now() <= RENEW_BEFORE:
            return None
        return token

    def current(self, key: InstallationKey, app: AppInstallation) -> MintedToken:
        """``key``'s token, minting one when none is held, the held one is due for renewal, or it was invalidated. A
        token just minted is answered even inside :data:`RENEW_BEFORE`, so a short-lived one is no mint loop."""
        token = self.fresh(key)
        if token is not None:
            return token
        with self._lock(key):
            token = self.fresh(key)
            if token is not None:
                return token
            minted = self._mint(key, app)
            with self._guard:
                for held in [k for k in self._tokens if _same_but_revision(k, key)]:
                    del self._tokens[held]
                self._tokens[key] = minted
            return minted

    def invalidate(self, key: InstallationKey, token: MintedToken) -> None:
        """Drop ``token`` if it is still ``key``'s — a token minted since is kept."""
        with self._guard:
            if self._tokens.get(key) is token:
                del self._tokens[key]

    def _lock(self, key: InstallationKey) -> threading.Lock:
        with self._guard:
            return self._locks.setdefault(key, threading.Lock())

    def _mint(self, key: InstallationKey, app: AppInstallation) -> MintedToken:
        installation = self.installation(key, app)
        response = self._call(
            app,
            "POST",
            f"/app/installations/{app.installation_id}/access_tokens",
            {"permissions": app_permissions(key.scope, installation.permissions)},
        )
        missing = _missing_permissions(key.scope, installation.permissions) or APP_REQUIRED_PERMISSIONS[key.scope]
        payload = self._answer(response, app, "mint_token", _MINT, missing=missing)
        try:
            token = str(payload["token"])
            expires_at = datetime.fromisoformat(str(payload["expires_at"]))
        except (KeyError, ValueError) as exc:
            raise httpx2.DecodingError("unreadable installation token answer") from exc
        granted = as_table(payload.get("permissions")) or {}
        return MintedToken(SecretValue(token), expires_at, {str(k): str(v) for k, v in granted.items()})

    def _call(
        self, app: AppInstallation, method: str, path: str, body: dict[str, object] | None = None
    ) -> httpx2.Response:
        try:
            signed = app_jwt(app.app_id, app.private_key, self._clock.now())
        except (ValueError, TypeError, jwt.PyJWTError):
            raise ForgeMintRefused(
                ClassifiedRefusal(
                    AuthRefusal.KEY_REJECTED,
                    ForgeDiagnostic(
                        status=0,
                        operation="sign_app_jwt",
                        endpoint=f"{method} {path}",
                        repository=None,
                        message="the App's private key could not sign a JWT",
                        documentation_url=None,
                        request_id=None,
                        accepted_permissions=None,
                        credential=app.credential.slug,
                        binding=app.credential.binding,
                        variant=app.credential.variant,
                    ),
                )
            ) from None
        with httpx2.Client(
            base_url=app.api_base.rstrip("/"), transport=self._transport, timeout=APP_CALL_TIMEOUT_SECONDS
        ) as client:
            return client.request(
                method,
                path,
                json=body,
                headers={"Authorization": f"Bearer {signed}", "Accept": "application/vnd.github+json"},
            )

    @staticmethod
    def _answer(
        response: httpx2.Response,
        app: AppInstallation,
        operation: str,
        endpoint: str,
        *,
        missing: Mapping[str, str] | None = None,
    ) -> dict[str, object]:
        """A 2xx answer's body; :class:`ForgeMintRefused` for a refusal GitHub classifies, and the
        ``httpx2.HTTPStatusError`` of any other answer."""
        if response.status_code < 300:
            try:
                return as_table(response.json()) or {}
            except ValueError as exc:
                raise httpx2.DecodingError(f"unreadable {operation} answer") from exc
        classified = classify_mint_refusal(
            response.status_code,
            response.headers,
            _said(response),
            documentation_url=_documentation(response),
            operation=operation,
            endpoint=endpoint,
            credential=app.credential,
            missing=_permission_list(missing) if missing is not None else None,
        )
        if classified is not None:
            raise ForgeMintRefused(classified)
        response.raise_for_status()
        raise AssertionError("unreachable")


def _same_but_revision(held: InstallationKey, key: InstallationKey) -> bool:
    return (held.forge_id, held.authority, held.scope) == (key.forge_id, key.authority, key.scope) and held != key


def _said(response: httpx2.Response) -> str:
    try:
        body = as_table(response.json()) or {}
    except ValueError:
        return response.text
    return str(body.get("message") or "")


def _documentation(response: httpx2.Response) -> str | None:
    try:
        body = as_table(response.json()) or {}
    except ValueError:
        return None
    url = body.get("documentation_url")
    return str(url) if url else None


class InstallationAuth(httpx2.Auth):
    """Sends ``key``'s installation token as ``Authorization: token …``, minting through ``tokens`` when it is due.
    A 401 drops the token it sent and the request goes once more with a fresh one; the answer to that is final.
    The event loop never waits on a mint: the async flow runs one on a worker thread."""

    requires_request_body = True

    def __init__(self, tokens: InstallationTokens, key: InstallationKey, app: AppInstallation) -> None:
        self._tokens = tokens
        self._key = key
        self._app = app

    def sync_auth_flow(self, request: httpx2.Request) -> Generator[httpx2.Request, httpx2.Response, None]:
        token = self._tokens.current(self._key, self._app)
        response = yield _carrying(request, token)
        if response.status_code == 401:
            self._recovering(token)
            yield _carrying(request, self._tokens.current(self._key, self._app))

    async def async_auth_flow(self, request: httpx2.Request) -> AsyncGenerator[httpx2.Request, httpx2.Response]:
        token = self._tokens.fresh(self._key) or await asyncio.to_thread(self._tokens.current, self._key, self._app)
        response = yield _carrying(request, token)
        if response.status_code == 401:
            self._recovering(token)
            yield _carrying(request, await asyncio.to_thread(self._tokens.current, self._key, self._app))

    def _recovering(self, token: MintedToken) -> None:
        _log.warning(
            "the forge refused an installation token; minting a fresh one",
            authority=self._key.authority,
            scope=str(self._key.scope),
        )
        self._tokens.invalidate(self._key, token)


def _carrying(request: httpx2.Request, token: MintedToken) -> httpx2.Request:
    request.headers["Authorization"] = f"token {token.token.expose()}"
    return request


def github_app_authorization(
    credential: ResolvedCredential,
    revealed: RevealedSecret,
    kind: frozenset[Capability],
    *,
    scope: ForgeScope,
    api_base: str,
    tokens: InstallationTokens,
) -> ForgeAuthorization:
    """``credential``'s authorization for ``scope`` over the App key ``revealed``, its offer narrowed by what the
    installation grants — read now, so a refused App or a missing or suspended installation raises
    :class:`ForgeMintRefused` before any request is made."""
    app = AppInstallation(
        api_base=api_base,
        app_id=int(cast(int, credential.settings["app_id"])),
        installation_id=int(cast(int, credential.settings["installation_id"])),
        private_key=revealed.value,
        credential=CallCredential(credential.slug, credential.binding, None),
    )
    key = InstallationKey(str(credential.forge_id), credential.authority, scope, revealed.revision)
    installation = tokens.installation(key, app)
    return ForgeAuthorization(
        credential_id=credential.credential_id,
        credential_slug=credential.slug,
        binding=credential.binding,
        variant=None,
        authority=credential.authority,
        offered=app_offers(scope, installation.permissions, kind),
        auth=InstallationAuth(tokens, key, app),
    )

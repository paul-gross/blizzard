"""A GitHub App's side of GitHub for tests, on one ``httpx2.MockTransport``: the installation read and the token mint,
each verifying the App's RS256 JWT against its public key, and every other route answering 200 to a live installation
token and 401 to anything else. It counts mints and reads, and can revoke a token or answer a mint or a read with any
status. Also: an App credential resolved for the boundary to build, and a forge reader holding one forge."""

from __future__ import annotations

import json
import threading
import time
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from functools import cache
from typing import Any

import httpx2
import jwt
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

from blizzard.foundation.clock import IClock
from blizzard.foundation.secrets.value import SecretValue
from blizzard.hub.domain.config.credential_bindings import (
    CREDENTIAL_BINDINGS,
    CredentialBinding,
    GitHubAppInstallationBinding,
)
from blizzard.hub.domain.config.credential_naming import CredentialId
from blizzard.hub.domain.config.credentials import ResolvedCredential
from blizzard.hub.domain.config.forges import ConfiguredForge, ForgeFields, ForgeId, ForgeSlug, IReadForgeRepository
from blizzard.hub.domain.config.secrets import RevealedSecret, SecretName
from blizzard.hub.domain.kernel.transactions import Transaction

API_BASE = "https://api.github.test"
APP_ID = 12
INSTALLATION_ID = 34
#: Every permission a landing or work-source token is minted with, and both optional ones.
FULL_GRANT: Mapping[str, str] = {
    "metadata": "read",
    "contents": "write",
    "pull_requests": "write",
    "issues": "write",
    "checks": "read",
    "actions": "write",
}
#: Every binding a credential may name, the App's among them.
WITH_APP: Mapping[str, CredentialBinding] = {
    **CREDENTIAL_BINDINGS,
    GitHubAppInstallationBinding().name: GitHubAppInstallationBinding(),
}


@cache
def app_key_pem(generation: int = 0) -> str:
    """A private RSA key in PEM, one per ``generation``, made once per test run."""
    del generation
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    return key.private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()
    ).decode()


def _public_pem(private_pem: str) -> str:
    key = serialization.load_pem_private_key(private_pem.encode(), password=None)
    return (
        key.public_key()
        .public_bytes(serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo)
        .decode()
    )


#: What every route but the App's answers 200 with: enough for a repository, an issue, and a branch tip.
_REPOSITORY_BODY = {"full_name": "acme/widget", "body": "", "title": "t", "object": {"sha": "abc"}}


def _answer(status: int, body: Any = None) -> httpx2.Response:
    return httpx2.Response(status, content=b"" if body is None else json.dumps(body).encode())


@dataclass
class _Installation:
    permissions: dict[str, str]
    suspended: bool = False


@dataclass
class AppGitHub:
    """The fake. ``lifetime`` is each minted token's; ``mint_delay`` holds every mint that long, so concurrent callers
    overlap it. ``mint_status`` and ``read_status`` force those answers' status; ``always_401`` refuses every other
    route's request whatever it carries."""

    clock: IClock
    private_pem: str = field(default_factory=app_key_pem)
    lifetime: timedelta = timedelta(hours=1)
    mint_delay: float = 0.0
    mint_status: int | None = None
    read_status: int | None = None
    always_401: bool = False
    rate_remaining: int | None = None
    installations: dict[int, _Installation] = field(
        default_factory=lambda: {INSTALLATION_ID: _Installation(dict(FULL_GRANT))}
    )
    mints: list[dict[str, Any]] = field(default_factory=list[dict[str, Any]])
    reads: int = 0
    jwts: list[str] = field(default_factory=list[str])
    tokens: dict[str, datetime] = field(default_factory=dict[str, datetime])
    sent: list[str] = field(default_factory=list[str])
    _lock: threading.Lock = field(default_factory=threading.Lock)

    @property
    def transport(self) -> httpx2.MockTransport:
        return httpx2.MockTransport(self._handle)

    def grant(self, permissions: Mapping[str, str], *, installation_id: int = INSTALLATION_ID) -> None:
        self.installations[installation_id] = _Installation(dict(permissions))

    def suspend(self, installation_id: int = INSTALLATION_ID) -> None:
        self.installations[installation_id].suspended = True

    def revoke(self, token: str) -> None:
        with self._lock:
            self.tokens.pop(token, None)

    def _handle(self, request: httpx2.Request) -> httpx2.Response:
        path = request.url.path
        if path.startswith("/app/installations/"):
            return self._app_route(request)
        header = request.headers.get("Authorization", "")
        with self._lock:
            self.sent.append(header)
            expires = self.tokens.get(header.removeprefix("token "))
        if self.always_401 or expires is None or self.clock.now() >= expires:
            return _answer(401, {"message": "Bad credentials"})
        answer = _answer(200, _REPOSITORY_BODY if "issues" not in path else {})
        if self.rate_remaining is not None:
            reset = int((self.clock.now() + timedelta(hours=1)).timestamp())
            answer.headers.update(
                {
                    "x-ratelimit-limit": "5000",
                    "x-ratelimit-remaining": str(self.rate_remaining),
                    "x-ratelimit-reset": str(reset),
                }
            )
        return answer

    def _app_route(self, request: httpx2.Request) -> httpx2.Response:
        signed = request.headers.get("Authorization", "").removeprefix("Bearer ")
        with self._lock:
            self.jwts.append(signed)
        try:
            claims = jwt.decode(
                signed,
                _public_pem(self.private_pem),
                algorithms=["RS256"],
                options={"verify_exp": False, "verify_iat": False},
            )
        except jwt.PyJWTError:
            return _answer(401, {"message": "A JSON web token could not be decoded"})
        if int(claims["iss"]) != APP_ID:
            return _answer(401, {"message": "Integration not found"})
        parts = request.url.path.split("/")
        installation = self.installations.get(int(parts[3]))
        if request.method == "GET":
            with self._lock:
                self.reads += 1
            if self.read_status is not None:
                return _answer(self.read_status, {"message": "refused"})
            if installation is None:
                return _answer(404, {"message": "Not Found"})
            return _answer(
                200,
                {
                    "id": int(parts[3]),
                    "permissions": installation.permissions,
                    "suspended_at": "2026-11-01T00:00:00Z" if installation.suspended else None,
                },
            )
        if self.mint_delay:
            time.sleep(self.mint_delay)
        asked = json.loads(request.content or b"{}").get("permissions")
        with self._lock:
            self.mints.append({"installation": int(parts[3]), "permissions": asked})
        if self.mint_status is not None:
            return _answer(self.mint_status, {"message": "refused"})
        if installation is None:
            return _answer(404, {"message": "Not Found"})
        if installation.suspended:
            return _answer(403, {"message": "This installation has been suspended"})
        with self._lock:
            token = f"ghs_minted{len(self.mints)}x{int(parts[3])}"
            expires_at = self.clock.now() + self.lifetime
            self.tokens[token] = expires_at
        return _answer(
            201,
            {"token": token, "expires_at": expires_at.isoformat().replace("+00:00", "Z"), "permissions": asked or {}},
        )


class AppKeyReader:
    """Reveals the App's key under ``revision``, which a test moves to replace the key."""

    def __init__(self, pem: str, *, revision: int = 1) -> None:
        self.pem = pem
        self.revision = revision

    def reveal(self, name: SecretName) -> RevealedSecret:
        del name
        return RevealedSecret(SecretValue(self.pem), self.revision)


def app_credential(
    *,
    forge_id: str = "frg_github",
    installation_id: int = INSTALLATION_ID,
    slug: str = "app",
) -> ResolvedCredential:
    """A resolved ``github.app-installation`` credential over the secret ``app-key``."""
    settings = {"app_id": APP_ID, "installation_id": installation_id}
    return ResolvedCredential(
        credential_id=CredentialId(f"cred_{slug}"),
        forge_id=ForgeId(forge_id),
        binding="github.app-installation",
        secret_name="app-key",
        settings=settings,
        revision=1,
        authority=GitHubAppInstallationBinding().authority("app-key", settings),
        slug=slug,
    )


class Forges(IReadForgeRepository):
    """A forge reader answering every forge id with a GitHub forge at ``api_base``."""

    def __init__(self, api_base: str = API_BASE) -> None:
        self._api_base = api_base

    def get(self, forge_id: ForgeId) -> ConfiguredForge | None:
        return ConfiguredForge(
            forge_id=forge_id,
            slug="github",
            name="GitHub",
            kind="github",
            fields=ForgeFields(self._api_base, None),
            revision=1,
            created_at=datetime(2026, 11, 1, tzinfo=UTC),
            created_by="op",
        )

    def get_many(
        self, forge_ids: list[ForgeId], *, transaction: Transaction | None = None
    ) -> dict[ForgeId, ConfiguredForge]:
        raise NotImplementedError

    def list_all(self, *, include_retired: bool) -> list[ConfiguredForge]:
        raise NotImplementedError

    def get_by_slug(self, slug: ForgeSlug) -> ConfiguredForge | None:
        raise NotImplementedError

    def get_by_former_slug(self, slug: ForgeSlug) -> ConfiguredForge | None:
        raise NotImplementedError

    def get_by_api_base(self, api_base: str) -> ConfiguredForge | None:
        raise NotImplementedError

    def resolve(self, value: str) -> ConfiguredForge | None:
        raise NotImplementedError

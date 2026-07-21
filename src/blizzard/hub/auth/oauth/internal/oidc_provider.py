"""The ``oidc`` conformer — issuer discovery, code exchange, ``id_token`` signature
verification (issue #92, package-private).

All ``httpx``/JWT usage is confined here (``bzh:dependency-inversion``); the route
sees only :class:`~blizzard.hub.auth.oauth.provider.IOAuthProvider`. Discovery (the
issuer's ``.well-known/openid-configuration``) is fetched lazily on first use and
cached for the conformer's lifetime — a hub process reuses one instance for its whole
run, and a discovery document does not change under it.
"""

from __future__ import annotations

from urllib.parse import urlencode

import httpx
import jwt
from jwt.algorithms import RSAAlgorithm

from blizzard.foundation.logging import get_logger
from blizzard.hub.auth.models import ProviderIdentity
from blizzard.hub.auth.oauth.provider import OAuthExchangeError

_log = get_logger("blizzard.hub.auth.oauth")


class OidcProvider:
    """A generic OIDC provider — discovery + authorization-code flow + ``id_token``
    signature verification against the issuer's published JWKS."""

    type = "oidc"

    def __init__(
        self,
        *,
        name: str,
        display_name: str,
        issuer: str,
        client_id: str,
        client_secret: str,
        http_client: httpx.Client,
    ) -> None:
        self.name = name
        self.display_name = display_name
        self._issuer = issuer.rstrip("/")
        self._client_id = client_id
        self._client_secret = client_secret
        self._http = http_client
        self._discovery: dict[str, object] | None = None

    def _discover(self) -> dict[str, object]:
        discovery = self._discovery
        if discovery is None:
            resp = self._http.get(f"{self._issuer}/.well-known/openid-configuration")
            resp.raise_for_status()
            discovery = resp.json()
            self._discovery = discovery
        return discovery

    def authorize_url(self, *, state: str, redirect_uri: str) -> str:
        discovery = self._discover()
        params = {
            "response_type": "code",
            "client_id": self._client_id,
            "redirect_uri": redirect_uri,
            "scope": "openid email profile",
            "state": state,
        }
        return f"{discovery['authorization_endpoint']}?{urlencode(params)}"

    def exchange(self, *, code: str, redirect_uri: str) -> ProviderIdentity:
        try:
            discovery = self._discover()
            token_resp = self._http.post(
                str(discovery["token_endpoint"]),
                data={
                    "grant_type": "authorization_code",
                    "code": code,
                    "redirect_uri": redirect_uri,
                    "client_id": self._client_id,
                    "client_secret": self._client_secret,
                },
            )
            token_resp.raise_for_status()
            id_token = token_resp.json()["id_token"]
            claims = self._verify_id_token(id_token, str(discovery["jwks_uri"]))
        except (httpx.HTTPError, KeyError, jwt.PyJWTError) as exc:
            _log.warning("oidc exchange failed", provider=self.name, detail=str(exc))
            raise OAuthExchangeError(f"oidc exchange failed for provider {self.name!r}") from exc
        subject = claims.get("sub")
        if not subject:
            raise OAuthExchangeError(f"oidc id_token for provider {self.name!r} carries no 'sub' claim")
        handle = claims.get("preferred_username") or claims.get("name") or str(subject)
        email = claims.get("email")
        return ProviderIdentity(
            subject=str(subject),
            handle=str(handle),
            email=str(email) if email is not None else None,
            email_verified=bool(claims.get("email_verified", False)),
        )

    def _verify_id_token(self, id_token: str, jwks_uri: str) -> dict[str, object]:
        header = jwt.get_unverified_header(id_token)
        jwks_resp = self._http.get(jwks_uri)
        jwks_resp.raise_for_status()
        keys = jwks_resp.json().get("keys", [])
        kid = header.get("kid")
        for jwk in keys:
            if kid is not None and jwk.get("kid") != kid:
                continue
            algorithms = self._trusted_algorithms(jwk)
            public_key = RSAAlgorithm.from_jwk(jwk)
            return jwt.decode(
                id_token,
                key=public_key,  # type: ignore[arg-type]
                algorithms=algorithms,
                audience=self._client_id,
                issuer=self._issuer,
            )
        raise OAuthExchangeError(f"no JWKS key matches id_token kid {kid!r} for provider {self.name!r}")

    def _trusted_algorithms(self, jwk: dict[str, object]) -> list[str]:
        # The accepted algorithm(s) must come from a source the issuer controls, never
        # the attacker-supplied token header (``jwt.get_unverified_header``) — an
        # attacker who controls the header can otherwise pick an algorithm (e.g. an
        # RS256-to-HS256 confusion attack, keying HMAC off the RSA public key) that
        # turns the verification into a forgeable one. Prefer the JWKS key's own
        # ``alg`` member, then the discovery document's advertised signing algorithms,
        # and only fall back to ``RS256`` when neither says anything.
        jwk_alg = jwk.get("alg")
        if jwk_alg:
            return [str(jwk_alg)]
        supported = self._discover().get("id_token_signing_alg_values_supported")
        if isinstance(supported, list) and supported:
            return [str(alg) for alg in supported]
        return ["RS256"]

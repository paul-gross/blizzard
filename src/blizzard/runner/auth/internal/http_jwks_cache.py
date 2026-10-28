"""The runner's cached view of the hub's published JWKS.

Fetched lazily and cached by ``kid``; a :meth:`JwksCache.key_for` miss triggers exactly
one re-fetch, so a rotated key is picked up with no restart. All ``httpx2``/JWK-parsing
usage is confined here (``bzh:dependency-inversion``)."""

from __future__ import annotations

from typing import cast

import httpx2
from cryptography.hazmat.primitives.asymmetric.rsa import RSAPublicKey
from jwt.algorithms import RSAAlgorithm

from blizzard.foundation.logging import get_logger

_log = get_logger("blizzard.runner.auth")


class JwksCache:
    """A per-process cache of the hub's current + previous public signing keys."""

    def __init__(self, http_client: httpx2.Client, jwks_url: str) -> None:
        self._http = http_client
        self._url = jwks_url
        self._keys: dict[str, RSAPublicKey] = {}

    def key_for(self, kid: str) -> RSAPublicKey | None:
        """The public key for ``kid``, refreshing from the hub once on a cache miss —
        never on a hit, so a steady-state validation costs no network call."""
        if kid not in self._keys:
            self._refresh()
        return self._keys.get(kid)

    def _refresh(self) -> None:
        try:
            resp = self._http.get(self._url)
            resp.raise_for_status()
        except httpx2.HTTPError as exc:
            _log.warning("jwks fetch failed", url=self._url, detail=str(exc))
            return
        keys: dict[str, RSAPublicKey] = {}
        for jwk in resp.json().get("keys", []):
            kid = jwk.get("kid")
            if not kid:
                continue
            keys[kid] = cast(RSAPublicKey, RSAAlgorithm.from_jwk(jwk))
        self._keys = keys

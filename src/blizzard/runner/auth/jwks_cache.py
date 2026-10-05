"""The hub-JWKS key seam a federation token is verified against: the signing key for a ``kid``, or
``None`` when the hub publishes none. The cached HTTP binding is package-private
(:mod:`.internal.http_jwks_cache`)."""

from __future__ import annotations

from typing import Protocol

from cryptography.hazmat.primitives.asymmetric.rsa import RSAPublicKey


class IJwksCache(Protocol):
    def key_for(self, kid: str) -> RSAPublicKey | None:
        """The hub's public signing key for ``kid``, or ``None`` when it publishes none."""
        ...

"""The hub-JWKS key seam: the signing key for a ``kid``, or ``None`` when the hub publishes none."""

from __future__ import annotations

from typing import Protocol

from cryptography.hazmat.primitives.asymmetric.rsa import RSAPublicKey


class IJwksCache(Protocol):
    def key_for(self, kid: str) -> RSAPublicKey | None:
        """The hub's public signing key for ``kid``, or ``None`` when it publishes none."""
        ...

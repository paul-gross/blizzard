from __future__ import annotations

from dataclasses import dataclass

from blizzard.foundation.hashing import Sha256Hex
from blizzard.foundation.roles import domain_model


@domain_model
@dataclass(frozen=True)
class TokenHash:
    """A fleet bearer token in plaintext — enrollment, route, or lease — and the digest
    it is stored and compared as."""

    plaintext: str

    @property
    def hex(self) -> str:
        return Sha256Hex(self.plaintext).hex

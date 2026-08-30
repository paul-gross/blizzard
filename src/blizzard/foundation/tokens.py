"""A fleet bearer token's digest, shared by every daemon."""

from __future__ import annotations

from dataclasses import dataclass

from blizzard.foundation.hashing import Sha256Hex


@dataclass(frozen=True)
class TokenHash:
    """A fleet bearer token in plaintext, and the digest it is stored and compared as."""

    plaintext: str

    @property
    def hex(self) -> str:
        return Sha256Hex(self.plaintext).hex

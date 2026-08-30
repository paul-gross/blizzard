"""A fleet bearer token's digest wrapper — shared by every daemon that mints, stores,
or checks one (enrollment, route, or lease)."""

from __future__ import annotations

from dataclasses import dataclass

from blizzard.foundation.hashing import Sha256Hex


@dataclass(frozen=True)
class TokenHash:
    """A fleet bearer token in plaintext — enrollment, route, or lease — and the digest
    it is stored and compared as (:class:`~blizzard.foundation.hashing.Sha256Hex`)."""

    plaintext: str

    @property
    def hex(self) -> str:
        return Sha256Hex(self.plaintext).hex

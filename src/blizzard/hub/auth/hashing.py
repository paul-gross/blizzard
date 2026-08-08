"""The session-id hasher (issue #91)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import ClassVar

from blizzard.foundation.hashing import Sha256Hex


@dataclass(frozen=True)
class SessionId:
    """A session id in plaintext — handed out once at mint, presented on every request."""

    #: ``secrets.token_urlsafe`` byte count for a minted session id — >= 128 bits (issue #91).
    BYTES: ClassVar[int] = 32

    plaintext: str

    @property
    def hash(self) -> str:
        """The sha256 hex digest a presented session id (cookie or bearer) is looked up by."""
        return Sha256Hex(self.plaintext).hex

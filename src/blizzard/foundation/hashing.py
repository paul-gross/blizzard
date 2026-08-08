"""One owner for the digest every plaintext credential is stored and compared as."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass


@dataclass(frozen=True)
class Sha256Hex:
    """A secret in plaintext and its sha256 hex digest, so mint and resolve cannot drift."""

    plaintext: str

    @property
    def hex(self) -> str:
        return hashlib.sha256(self.plaintext.encode("utf-8")).hexdigest()

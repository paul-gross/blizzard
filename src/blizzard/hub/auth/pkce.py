"""PKCE (RFC 7636) S256 challenge/verifier — the CLI public client's mandatory
proof-of-possession (issue #96, decision D6's ``client=cli``).

Dependency-free (``bzh:domain-core``), so both sides of the exchange derive the challenge
from this one module and cannot drift.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
from dataclasses import dataclass

#: ``secrets.token_urlsafe`` byte count for a minted verifier — well inside RFC 7636 §4.1's
#: 43..128 character range.
_VERIFIER_BYTES = 48


@dataclass(frozen=True)
class Pkce:
    """One ``code_verifier`` and the ``code_challenge`` it must hash to."""

    verifier: str

    @classmethod
    def new(cls) -> Pkce:
        return cls(secrets.token_urlsafe(_VERIFIER_BYTES))

    @property
    def challenge(self) -> str:
        """``BASE64URL-ENCODE(SHA256(code_verifier))`` with padding stripped, per RFC 7636 §4.2."""
        digest = hashlib.sha256(self.verifier.encode("ascii")).digest()
        return base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")

    def matches(self, code_challenge: str) -> bool:
        """The exchange route's own check. Comparing digests rather than raw secrets, a
        constant-time compare carries no extra weight — but is used regardless."""
        return hmac.compare_digest(self.challenge, code_challenge)

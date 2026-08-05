"""PKCE (RFC 7636) S256 challenge/verifier — the CLI public client's mandatory
proof-of-possession (issue #96, decision D6's ``client=cli``).

Dependency-free (``bzh:domain-core``), so both sides of the exchange derive the challenge
from this one module and cannot drift.
"""

from __future__ import annotations

import base64
import hashlib
import hmac


def challenge_from_verifier(code_verifier: str) -> str:
    """The S256 ``code_challenge`` derived from a ``code_verifier`` —
    ``BASE64URL-ENCODE(SHA256(code_verifier))`` with padding stripped, per RFC 7636 §4.2."""
    digest = hashlib.sha256(code_verifier.encode("ascii")).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")


def verify_code_challenge(code_verifier: str, code_challenge: str) -> bool:
    """``True`` iff ``code_verifier`` hashes to the stored ``code_challenge`` — the
    exchange route's own check, comparing digests (not raw secrets) so a
    constant-time compare carries no extra weight either way, but is used regardless
    (cheap, and the conventional shape for this kind of check)."""
    return hmac.compare_digest(challenge_from_verifier(code_verifier), code_challenge)

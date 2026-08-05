"""The session-id hasher — shared by mint and resolve so the two can never drift onto
different digests of the same plaintext (issue #91).
"""

from __future__ import annotations

import hashlib

#: ``secrets.token_urlsafe`` byte count for a minted session id — >= 128 bits (issue #91).
SESSION_ID_BYTES = 32


def hash_session_id(session_id: str) -> str:
    """The sha256 hex digest a presented session id (cookie or bearer) is looked up by."""
    return hashlib.sha256(session_id.encode("utf-8")).hexdigest()

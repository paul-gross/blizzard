"""The session-id hasher — shared by mint and resolve so the two can never drift onto
different digests of the same plaintext (issue #91).

Its own function rather than an import of ``hub/domain/enrollment.py``'s ``hash_token``:
a session id and a runner bearer token are different credential kinds that happen to share
a hashing scheme, and importing ``hub.auth.hashing`` should not pull in the
runner-enrollment domain module.
"""

from __future__ import annotations

import hashlib

#: ``secrets.token_urlsafe`` byte count for a minted session id — 32 bytes -> a
#: 43-character URL-safe token, matching the runner bearer token's own strength
#: (>= 128 bits, issue #91's AC).
SESSION_ID_BYTES = 32


def hash_session_id(session_id: str) -> str:
    """The sha256 hex digest a presented session id (cookie or bearer) is looked up by.

    The single hashing function both :meth:`~blizzard.hub.auth.service.AuthService.mint_session`
    and ``hub/api/auth_session.py``'s ``resolve_identity`` call, so the two can never
    resolve a plaintext against a differently-computed digest."""
    return hashlib.sha256(session_id.encode("utf-8")).hexdigest()

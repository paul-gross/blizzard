"""The session repository seam — read/write Protocols (issue #91,
``bzh:repository-split``).

``resolve_identity`` (``hub/api/auth_session.py``) is the edge's read: it looks up a
session by its **hashed** id via the read Protocol, then hands the loaded
:class:`~blizzard.hub.auth.models.Session` object into
:meth:`~blizzard.hub.auth.service.AuthService.touch_session` for the sliding-expiry
write (``bzh:domain-takes-objects``) — the edge never mutates directly
(``bzh:controller-read-only``).
"""

from __future__ import annotations

from datetime import datetime
from typing import Protocol

from blizzard.hub.auth.models import Session


class IReadSessionRepository(Protocol):
    """Read-only session lookups, keyed by the session id's sha256 hash."""

    def get_by_hash(self, id_hash: str) -> Session | None: ...


class IWriteSessionRepository(IReadSessionRepository, Protocol):
    """Read-write session access — only the domain layer depends on this variant."""

    def create(self, session: Session) -> None: ...

    def touch(self, id_hash: str, *, last_seen_at: datetime, expires_at: datetime) -> None:
        """Slide a session's expiry — the write ``AuthService.touch_session`` delegates to
        on every resolve."""
        ...

    def delete(self, id_hash: str) -> None:
        """Revoke a session outright (logout, #92) — landed now so the Protocol
        surface is stable; nothing calls it in this phase."""
        ...

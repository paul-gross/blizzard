"""The session repository seam — read/write Protocols (issue #91,
``bzh:repository-split``).

Sessions are looked up by their **hashed** id; the write side is reserved for the
service layer (``bzh:controller-read-only``).
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
        """Revoke a session outright (logout, #92)."""
        ...

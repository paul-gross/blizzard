"""The user repository seam — read/write Protocols (issue #91, ``bzh:repository-split``).

The concrete SQLAlchemy adapter lives at ``internal/user_repository.py``
(``bzh:dependency-inversion``); this module holds only the Protocol pair a
collaborator depends on by the narrowest variant its job needs
(``bzh:controller-read-only``).
"""

from __future__ import annotations

from typing import Protocol

from blizzard.hub.auth.models import User


class IReadUserRepository(Protocol):
    """Read-only user lookups."""

    def get(self, user_id: str) -> User | None: ...
    def get_by_username(self, username: str) -> User | None: ...
    def get_by_email(self, email: str) -> User | None: ...
    def username_exists(self, username: str) -> bool: ...


class IWriteUserRepository(IReadUserRepository, Protocol):
    """Read-write user access — only the domain layer depends on this variant."""

    def create(self, user: User) -> None: ...

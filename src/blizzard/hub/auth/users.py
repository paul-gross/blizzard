"""The user repository seam — read/write Protocols (issue #91, ``bzh:repository-split``).

The concrete SQLAlchemy adapter lives at ``internal/user_repository.py``
(``bzh:dependency-inversion``); this module holds only the Protocol pair a
collaborator depends on by the narrowest variant its job needs
(``bzh:controller-read-only``).
"""

from __future__ import annotations

from typing import Protocol

from blizzard.auth_core import Role
from blizzard.hub.auth.models import User


class IReadUserRepository(Protocol):
    """Read-only user lookups."""

    def get(self, user_id: str) -> User | None: ...
    def get_by_username(self, username: str) -> User | None: ...
    def get_by_email(self, email: str) -> User | None: ...
    def username_exists(self, username: str) -> bool: ...

    def list_all(self) -> list[User]:
        """Every user, for the admin page's own listing (issue #94) — the one caller
        that ever needs the whole table rather than a single lookup."""
        ...


class IWriteUserRepository(IReadUserRepository, Protocol):
    """Read-write user access — only the domain layer depends on this variant."""

    def create(self, user: User) -> None: ...

    def update_role(self, user_id: str, role: Role) -> None:
        """Set ``user_id``'s stored role in place (issue #94's role-assignment API and
        superuser-bootstrap lifecycle) — the write ``AuthService`` delegates to after
        its own rule checks have already passed."""
        ...

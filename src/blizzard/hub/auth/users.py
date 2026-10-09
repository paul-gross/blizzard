"""The user repository seam — read/write Protocols (``bzh:repository-split``).

The concrete SQLAlchemy adapter lives at ``src/blizzard/hub/auth/internal/user_repository.py``
(``bzh:dependency-inversion``); this module holds only the Protocol pair, depended on
by the narrowest variant a job needs (``bzh:controller-read-only``)."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Protocol

from blizzard.hub.auth.models import User


class IReadUserRepository(Protocol):
    """Read-only user lookups."""

    def get(self, user_id: str) -> User | None: ...
    def get_by_username(self, username: str) -> User | None: ...
    def get_by_email(self, email: str) -> User | None: ...

    def list_by_email_any_case(self, email: str) -> list[User]:
        """Every user whose email equals ``email`` compared case-insensitively, oldest first."""
        ...

    def username_exists(self, username: str) -> bool: ...

    def get_many(self, user_ids: Sequence[str]) -> dict[str, User]:
        """`get`'s batched sibling — every id in `user_ids` that names an existing user,
        a missing id simply absent from the result rather than raising."""
        ...

    def list_all(self) -> list[User]:
        """Every user in the table."""
        ...


class IWriteUserRepository(IReadUserRepository, Protocol):
    """Adds the user writes."""

    def create(self, user: User) -> None: ...

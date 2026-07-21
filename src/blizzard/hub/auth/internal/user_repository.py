"""SQLAlchemy adapter for the user repository seam (package-private, issue #91).

All ``sqlalchemy`` usage is confined here (``bzh:dependency-inversion``); the domain
sees only :class:`~blizzard.hub.auth.models.User`.
"""

from __future__ import annotations

from sqlalchemy import Engine, insert, select
from sqlalchemy.exc import IntegrityError

from blizzard.auth_core import Role
from blizzard.hub.auth.errors import RepoErrorFactory
from blizzard.hub.auth.models import User
from blizzard.hub.auth.users import IWriteUserRepository
from blizzard.hub.store import schema as s


class UserRepository:
    """Read-write user adapter over the hub store engine."""

    def __init__(self, engine: Engine, errors: RepoErrorFactory) -> None:
        self._engine = engine
        self._errors = errors

    # --- reads ----------------------------------------------------------

    def get(self, user_id: str) -> User | None:
        with self._engine.connect() as conn:
            row = conn.execute(select(s.users).where(s.users.c.id == user_id)).one_or_none()
            return self._user(row) if row is not None else None

    def get_by_username(self, username: str) -> User | None:
        with self._engine.connect() as conn:
            row = conn.execute(select(s.users).where(s.users.c.username == username)).one_or_none()
            return self._user(row) if row is not None else None

    def get_by_email(self, email: str) -> User | None:
        with self._engine.connect() as conn:
            row = conn.execute(select(s.users).where(s.users.c.email == email)).one_or_none()
            return self._user(row) if row is not None else None

    def username_exists(self, username: str) -> bool:
        with self._engine.connect() as conn:
            row = conn.execute(select(s.users.c.id).where(s.users.c.username == username)).one_or_none()
            return row is not None

    def list_all(self) -> list[User]:
        with self._engine.connect() as conn:
            rows = conn.execute(select(s.users).order_by(s.users.c.created_at)).all()
            return [self._user(row) for row in rows]

    # --- writes -----------------------------------------------------------

    def create(self, user: User) -> None:
        try:
            with self._engine.begin() as conn:
                conn.execute(
                    insert(s.users).values(
                        id=user.user_id,
                        username=user.username,
                        display_name=user.display_name,
                        email=user.email,
                        role=user.role.value,
                        created_at=user.created_at,
                    )
                )
        except IntegrityError as exc:
            raise self._errors.from_integrity_error(
                exc, f"failed to create user {user.username!r}", operation="create"
            ) from exc

    def update_role(self, user_id: str, role: Role) -> None:
        with self._engine.begin() as conn:
            conn.execute(s.users.update().where(s.users.c.id == user_id).values(role=role.value))

    # --- helpers ------------------------------------------------------------

    @staticmethod
    def _user(row) -> User:  # type: ignore[no-untyped-def]
        return User(
            user_id=row.id,
            username=row.username,
            display_name=row.display_name,
            email=row.email,
            role=Role(row.role),
            created_at=row.created_at,
        )


def _conforms_user_repository(x: UserRepository) -> IWriteUserRepository:
    return x

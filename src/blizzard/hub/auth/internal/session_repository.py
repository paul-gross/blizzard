"""SQLAlchemy adapter for the session repository seam (package-private, issue #91).

All ``sqlalchemy`` usage is confined here (``bzh:dependency-inversion``); the domain
sees only :class:`~blizzard.hub.auth.models.Session`.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import Engine, delete, insert, select
from sqlalchemy.exc import IntegrityError

from blizzard.hub.auth.errors import RepoErrorFactory
from blizzard.hub.auth.models import Session
from blizzard.hub.auth.sessions import IWriteSessionRepository
from blizzard.hub.store import schema as s


class SessionRepository:
    """Read-write session adapter over the hub store engine."""

    def __init__(self, engine: Engine, errors: RepoErrorFactory) -> None:
        self._engine = engine
        self._errors = errors

    # --- reads ----------------------------------------------------------

    def get_by_hash(self, id_hash: str) -> Session | None:
        with self._engine.connect() as conn:
            row = conn.execute(select(s.sessions).where(s.sessions.c.id_hash == id_hash)).one_or_none()
            return self._session(row) if row is not None else None

    # --- writes -----------------------------------------------------------

    def create(self, session: Session) -> None:
        try:
            with self._engine.begin() as conn:
                conn.execute(
                    insert(s.sessions).values(
                        id_hash=session.id_hash,
                        user_id=session.user_id,
                        created_at=session.created_at,
                        expires_at=session.expires_at,
                        last_seen_at=session.last_seen_at,
                    )
                )
        except IntegrityError as exc:
            raise self._errors.from_integrity_error(
                exc, "failed to create session (id-hash collision)", operation="create"
            ) from exc

    def touch(self, id_hash: str, *, last_seen_at: datetime, expires_at: datetime) -> None:
        with self._engine.begin() as conn:
            conn.execute(
                s.sessions.update()
                .where(s.sessions.c.id_hash == id_hash)
                .values(last_seen_at=last_seen_at, expires_at=expires_at)
            )

    def delete(self, id_hash: str) -> None:
        with self._engine.begin() as conn:
            conn.execute(delete(s.sessions).where(s.sessions.c.id_hash == id_hash))

    # --- helpers ------------------------------------------------------------

    @staticmethod
    def _session(row) -> Session:  # type: ignore[no-untyped-def]
        return Session(
            id_hash=row.id_hash,
            user_id=row.user_id,
            created_at=row.created_at,
            expires_at=row.expires_at,
            last_seen_at=row.last_seen_at,
        )


def _conforms_session_repository(x: SessionRepository) -> IWriteSessionRepository:
    return x

"""SQLAlchemy adapter for the ``auth_state`` repository seam (package-private, issue #92).

All ``sqlalchemy`` usage is confined here (``bzh:dependency-inversion``); the domain
sees only :class:`~blizzard.hub.auth.models.AuthStateEntry`.
"""

from __future__ import annotations

from sqlalchemy import Engine, delete, insert, select
from sqlalchemy.exc import IntegrityError

from blizzard.hub.auth.auth_state import IWriteAuthStateRepository
from blizzard.hub.auth.errors import RepoErrorFactory
from blizzard.hub.auth.models import AuthStateEntry
from blizzard.hub.store import schema as s


class AuthStateRepository:
    """Read-write ``auth_state`` adapter over the hub store engine."""

    def __init__(self, engine: Engine, errors: RepoErrorFactory) -> None:
        self._engine = engine
        self._errors = errors

    # --- reads ----------------------------------------------------------

    def get(self, state: str) -> AuthStateEntry | None:
        with self._engine.connect() as conn:
            row = conn.execute(select(s.auth_state).where(s.auth_state.c.state == state)).one_or_none()
            return self._entry(row) if row is not None else None

    # --- writes -----------------------------------------------------------

    def create(self, entry: AuthStateEntry) -> None:
        try:
            with self._engine.begin() as conn:
                conn.execute(
                    insert(s.auth_state).values(
                        state=entry.state,
                        kind=entry.kind,
                        provider_name=entry.provider_name,
                        return_to=entry.return_to,
                        code_challenge=entry.code_challenge,
                        created_at=entry.created_at,
                        expires_at=entry.expires_at,
                        user_id=entry.user_id,
                    )
                )
        except IntegrityError as exc:
            raise self._errors.from_integrity_error(
                exc, "failed to create auth_state entry (state collision)", operation="create"
            ) from exc

    def consume(self, state: str) -> AuthStateEntry | None:
        # The DELETE is the single-use gate (mirrors the jti cache's PK-insert
        # discipline, ``bzh:dependency-inversion``): a plain SELECT-then-DELETE lets
        # two racing callers both read the row before either deletes it, so both would
        # "win". Deleting with ``RETURNING`` makes the delete itself atomic with the
        # read — only the caller whose DELETE actually removed the row gets the entry
        # back; a racing loser's DELETE affects zero rows and returns ``None``.
        with self._engine.begin() as conn:
            row = conn.execute(
                delete(s.auth_state).where(s.auth_state.c.state == state).returning(*s.auth_state.c)
            ).one_or_none()
            return self._entry(row) if row is not None else None

    # --- helpers ------------------------------------------------------------

    @staticmethod
    def _entry(row) -> AuthStateEntry:  # type: ignore[no-untyped-def]
        return AuthStateEntry(
            state=row.state,
            kind=row.kind,
            provider_name=row.provider_name,
            return_to=row.return_to,
            code_challenge=row.code_challenge,
            created_at=row.created_at,
            expires_at=row.expires_at,
            user_id=row.user_id,
        )


def _conforms_auth_state_repository(x: AuthStateRepository) -> IWriteAuthStateRepository:
    return x

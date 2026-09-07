"""SQLAlchemy adapter for the identity-link repository seam (package-private, issue #91).

All ``sqlalchemy`` usage is confined here (``bzh:dependency-inversion``); the domain
sees only :class:`~blizzard.hub.auth.models.Identity`.
"""

from __future__ import annotations

from sqlalchemy import insert, select
from sqlalchemy.exc import IntegrityError

from blizzard.hub.auth.errors import RepoErrorFactory
from blizzard.hub.auth.identities import IWriteIdentityRepository
from blizzard.hub.auth.models import Identity
from blizzard.hub.store import schema as s
from blizzard.hub.store.errors import HubStoreConnections


class IdentityRepository:
    """Read-write identity-link adapter over the hub store."""

    def __init__(self, store: HubStoreConnections, errors: RepoErrorFactory) -> None:
        self._store = store
        self._errors = errors

    # --- reads ----------------------------------------------------------

    def get(self, provider_name: str, subject: str) -> Identity | None:
        with self._store.read("get") as conn:
            row = conn.execute(
                select(s.identities).where(
                    s.identities.c.provider_name == provider_name, s.identities.c.subject == subject
                )
            ).one_or_none()
            return self._identity(row) if row is not None else None

    def list_for_user(self, user_id: str) -> list[Identity]:
        with self._store.read("list_for_user") as conn:
            rows = conn.execute(
                select(s.identities).where(s.identities.c.user_id == user_id).order_by(s.identities.c.created_at)
            ).all()
            return [self._identity(row) for row in rows]

    def distinct_provider_names(self) -> set[str]:
        with self._store.read("distinct_provider_names") as conn:
            rows = conn.execute(select(s.identities.c.provider_name).distinct()).all()
            return {row.provider_name for row in rows}

    # --- writes -----------------------------------------------------------

    def link(self, identity: Identity) -> None:
        try:
            with self._store.write("link", expect=(IntegrityError,)) as conn:
                conn.execute(
                    insert(s.identities).values(
                        provider_name=identity.provider_name,
                        subject=identity.subject,
                        user_id=identity.user_id,
                        handle=identity.handle,
                        created_at=identity.created_at,
                    )
                )
        except IntegrityError as exc:
            raise self._errors.from_integrity_error(
                exc,
                f"failed to link identity {identity.provider_name!r}:{identity.subject!r}",
                operation="link",
            ) from exc

    def update_handle(self, provider_name: str, subject: str, *, handle: str) -> None:
        with self._store.write("update_handle") as conn:
            conn.execute(
                s.identities.update()
                .where(s.identities.c.provider_name == provider_name, s.identities.c.subject == subject)
                .values(handle=handle)
            )

    # --- helpers ------------------------------------------------------------

    @staticmethod
    def _identity(row) -> Identity:  # type: ignore[no-untyped-def]
        return Identity(
            provider_name=row.provider_name,
            subject=row.subject,
            user_id=row.user_id,
            handle=row.handle,
            created_at=row.created_at,
        )


def _conforms_identity_repository(x: IdentityRepository) -> IWriteIdentityRepository:
    return x

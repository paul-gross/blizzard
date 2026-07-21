"""SQLAlchemy adapter for the superuser-bootstrap repository seam (package-private,
issue #94).

All ``sqlalchemy`` usage is confined here (``bzh:dependency-inversion``); the domain
sees only :class:`~blizzard.hub.auth.models.SuperuserBootstrap`. There is at most one
row at rest: :meth:`upsert` deletes-then-inserts within one transaction rather than an
``ON CONFLICT`` upsert, keeping this adapter inside SQLAlchemy's portable DDL/DML
surface across the sqlite/postgres dialects this store targets.
"""

from __future__ import annotations

from sqlalchemy import Engine, delete, insert, select

from blizzard.hub.auth.models import SuperuserBootstrap
from blizzard.hub.auth.superuser_bootstrap import IWriteSuperuserBootstrapRepository
from blizzard.hub.store import schema as s


class SuperuserBootstrapRepository:
    """Read-write adapter over the ``superuser_bootstrap`` singleton row."""

    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    # --- reads ----------------------------------------------------------

    def get(self) -> SuperuserBootstrap | None:
        with self._engine.connect() as conn:
            row = conn.execute(select(s.superuser_bootstrap)).first()
            return self._bootstrap(row) if row is not None else None

    # --- writes -----------------------------------------------------------

    def upsert(self, bootstrap: SuperuserBootstrap) -> None:
        with self._engine.begin() as conn:
            conn.execute(delete(s.superuser_bootstrap))
            conn.execute(
                insert(s.superuser_bootstrap).values(
                    email=bootstrap.email,
                    claimed_user_id=bootstrap.claimed_user_id,
                    updated_at=bootstrap.updated_at,
                )
            )

    def clear(self) -> None:
        with self._engine.begin() as conn:
            conn.execute(delete(s.superuser_bootstrap))

    # --- helpers ------------------------------------------------------------

    @staticmethod
    def _bootstrap(row) -> SuperuserBootstrap:  # type: ignore[no-untyped-def]
        return SuperuserBootstrap(email=row.email, claimed_user_id=row.claimed_user_id, updated_at=row.updated_at)


def _conforms_superuser_bootstrap_repository(x: SuperuserBootstrapRepository) -> IWriteSuperuserBootstrapRepository:
    return x

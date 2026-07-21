"""SQLAlchemy adapter for the ``auth_facts`` repository seam (package-private, issue #92).

All ``sqlalchemy`` usage is confined here (``bzh:dependency-inversion``); the domain
sees only :class:`~blizzard.hub.auth.models.AuthFact`.
"""

from __future__ import annotations

from sqlalchemy import Engine, insert, select

from blizzard.hub.auth.facts import IWriteAuthFactsRepository
from blizzard.hub.auth.models import AuthFact
from blizzard.hub.store import schema as s


class AuthFactsRepository:
    """Append-only ``auth_facts`` adapter over the hub store engine."""

    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    def list_recent(self, *, limit: int = 50) -> list[AuthFact]:
        with self._engine.connect() as conn:
            rows = conn.execute(select(s.auth_facts).order_by(s.auth_facts.c.id.desc()).limit(limit)).all()
            return [self._fact(row) for row in rows]

    def create(self, fact: AuthFact) -> None:
        with self._engine.begin() as conn:
            conn.execute(
                insert(s.auth_facts).values(
                    kind=fact.kind,
                    actor=fact.actor,
                    subject=fact.subject,
                    detail=fact.detail,
                    recorded_at=fact.recorded_at,
                )
            )

    @staticmethod
    def _fact(row) -> AuthFact:  # type: ignore[no-untyped-def]
        return AuthFact(
            kind=row.kind, actor=row.actor, subject=row.subject, detail=row.detail, recorded_at=row.recorded_at
        )


def _conforms_auth_facts_repository(x: AuthFactsRepository) -> IWriteAuthFactsRepository:
    return x

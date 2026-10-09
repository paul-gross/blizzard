"""SQLAlchemy adapter for the hub-administrator repository seam (package-private).

All ``sqlalchemy`` usage is confined here (``bzh:dependency-inversion``); the domain sees only
:class:`~blizzard.hub.auth.hub_admins.HubAdminFact`. ``hub_admin_facts`` is global, so this adapter holds hub-level
connections."""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy import insert, select

from blizzard.hub.auth.hub_admins import HubAdminFact, IWriteHubAdminRepository
from blizzard.hub.store import schema as s
from blizzard.hub.store.errors import HubStoreConnections

_facts = s.hub_admin_facts


class HubAdminRepository:
    """Read-write ``hub_admin_facts`` adapter over the hub store."""

    def __init__(self, store: HubStoreConnections) -> None:
        self._store = store

    # --- reads ----------------------------------------------------------

    def is_hub_admin(self, user_id: str) -> bool:
        with self._store.read("is_hub_admin") as conn:
            granted = conn.execute(
                select(_facts.c.granted).where(_facts.c.user_id == user_id).order_by(_facts.c.id.desc()).limit(1)
            ).scalar_one_or_none()
            return bool(granted)

    def holders(self) -> list[str]:
        later = _facts.alias("later")
        superseded = sa.exists().where(later.c.user_id == _facts.c.user_id, later.c.id > _facts.c.id)
        with self._store.read("holders") as conn:
            return list(
                conn.execute(
                    select(_facts.c.user_id).where(_facts.c.granted.is_(True), ~superseded).order_by(_facts.c.user_id)
                ).scalars()
            )

    # --- writes -----------------------------------------------------------

    def record(self, fact: HubAdminFact) -> None:
        with self._store.write("record") as conn:
            conn.execute(
                insert(_facts).values(
                    user_id=fact.user_id, granted=fact.granted, set_at=fact.set_at, set_by=fact.set_by
                )
            )


def _conforms_hub_admin_repository(x: HubAdminRepository) -> IWriteHubAdminRepository:
    return x

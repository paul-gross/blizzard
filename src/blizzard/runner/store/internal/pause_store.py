"""SQLAlchemy adapter for the pause-brake repository seam (package-private)."""

from __future__ import annotations

from datetime import datetime

import sqlalchemy as sa
from sqlalchemy import func

from blizzard.foundation.logging import get_logger
from blizzard.runner.store.errors import RunnerStoreConnections
from blizzard.runner.store.internal.base import open_pause_park, pause_parked_lease_ids
from blizzard.runner.store.schema import (
    hub_control,
    local_pause_facts,
    outbound_buffer,
    pause_park_resumes,
    pause_parks,
)
from blizzard.runner.throttle.pause import IWritePauseRepository, PausePark

_log = get_logger("blizzard.runner.store")


class PauseStore:
    """Read-write pause-brake adapter over the runner store engine."""

    def __init__(self, store: RunnerStoreConnections) -> None:
        self._store = store

    def hub_contact_at(self) -> datetime | None:
        rows = self._store.all(
            self._store.select(hub_control, hub_control.c.updated_at).order_by(hub_control.c.id.desc()).limit(1)
        )
        return rows[0].updated_at if rows else None

    def hub_paused(self) -> bool:
        rows = self._store.all(
            self._store.select(hub_control, hub_control.c.paused).order_by(hub_control.c.id.desc()).limit(1)
        )
        return bool(rows[0].paused) if rows else False

    def local_paused(self) -> bool:
        rows = self._store.all(
            self._store.select(local_pause_facts, local_pause_facts.c.paused)
            .order_by(local_pause_facts.c.id.desc())
            .limit(1)
        )
        return bool(rows[0].paused) if rows else False

    def local_pause_reason(self) -> str | None:
        rows = self._store.all(
            self._store.select(local_pause_facts, local_pause_facts.c.reason)
            .order_by(local_pause_facts.c.id.desc())
            .limit(1)
        )
        return rows[0].reason if rows else None

    def pause_parked_lease_ids(self) -> set[str]:
        return {str(r.lease_id) for r in self._store.all(pause_parked_lease_ids(self._store))}

    def open_pause_parks(self) -> dict[str, PausePark]:
        # The newest open park per lease, grouped in SQL so the read costs the leases parked, not the re-park history.
        st = self._store
        newest = (
            st.select(pause_parks, func.max(pause_parks.c.id).label("id"))
            .where(open_pause_park(st))
            .group_by(pause_parks.c.lease_id)
            .subquery()
        )
        rows = st.all(
            st.select(
                sa.join(pause_parks, newest, pause_parks.c.id == newest.c.id),
                pause_parks.c.lease_id,
                pause_parks.c.chunk_id,
                pause_parks.c.parked_at,
                pause_parks.c.interrupted_elicitation_id,
            )
        )
        parks: dict[str, PausePark] = {}
        for r in rows:
            parks[str(r.lease_id)] = PausePark(
                lease_id=str(r.lease_id),
                chunk_id=str(r.chunk_id),
                parked_at=r.parked_at,
                interrupted_elicitation_id=(
                    int(r.interrupted_elicitation_id) if r.interrupted_elicitation_id is not None else None
                ),
            )
        return parks

    def set_hub_paused(self, *, paused: bool, at: datetime) -> None:
        with self._store.begin() as conn:
            existing = conn.execute(self._store.select(hub_control, hub_control.c.id).limit(1)).one_or_none()
            if existing is None:
                conn.execute(self._store.insert(hub_control).values(paused=paused, updated_at=at))
            else:
                conn.execute(
                    self._store.update(hub_control)
                    .where(hub_control.c.id == existing.id)
                    .values(paused=paused, updated_at=at)
                )

    def record_local_pause(
        self,
        *,
        paused: bool,
        at: datetime,
        by: str,
        report_kind: str,
        report_payload: str,
        reason: str | None = None,
    ) -> int:
        # Both inserts, one transaction: two would leave a `kill -9` window where the runner
        # has stopped claiming and the hub is never told.
        with self._store.begin() as conn:
            conn.execute(
                self._store.insert(local_pause_facts).values(paused=paused, set_at=at, set_by=by, reason=reason)
            )
            result = conn.execute(
                self._store.insert(outbound_buffer).values(
                    kind=report_kind, chunk_id=None, lease_id=None, payload=report_payload, created_at=at
                )
            )
        _log.info("local pause fact recorded", paused=paused, set_by=by, report=report_kind)
        key = result.inserted_primary_key
        return int(key[0]) if key is not None else 0

    def record_pause_park(
        self, *, lease_id: str, chunk_id: str, parked_at: datetime, interrupted_elicitation_id: int | None = None
    ) -> None:
        with self._store.begin() as conn:
            conn.execute(
                self._store.insert(pause_parks).values(
                    lease_id=lease_id,
                    chunk_id=chunk_id,
                    parked_at=parked_at,
                    interrupted_elicitation_id=interrupted_elicitation_id,
                )
            )
        _log.info(
            "chunk parked on operator pause",
            lease_id=lease_id,
            chunk_id=chunk_id,
            interrupted_elicitation_id=interrupted_elicitation_id,
        )

    def record_pause_park_resume(self, *, lease_id: str, resumed_at: datetime) -> None:
        with self._store.begin() as conn:
            conn.execute(self._store.insert(pause_park_resumes).values(lease_id=lease_id, resumed_at=resumed_at))
        _log.info("pause park resumed", lease_id=lease_id)


def _conforms_pause_store(x: PauseStore) -> IWritePauseRepository:
    return x

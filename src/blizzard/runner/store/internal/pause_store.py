"""SQLAlchemy adapter for the pause-brake/daemon-liveness repository seam (package-private,
blizzard#410)."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import func, select

from blizzard.foundation.logging import get_logger
from blizzard.runner.domain.pause import IWritePauseRepository
from blizzard.runner.store.internal.base import PAUSE_PARKED_LEASE_IDS, RunnerStoreConnections
from blizzard.runner.store.schema import (
    daemon_liveness,
    hub_control,
    local_pause_facts,
    outbound_buffer,
    pause_park_resumes,
    pause_parks,
)

_log = get_logger("blizzard.runner.store")


class PauseStore:
    """Read-write pause-brake and daemon-liveness adapter over the runner store engine."""

    def __init__(self, store: RunnerStoreConnections) -> None:
        self._store = store

    def hub_contact_at(self, runner_id: str) -> datetime | None:
        rows = self._store.all(select(hub_control.c.updated_at).where(hub_control.c.runner_id == runner_id))
        return rows[0].updated_at if rows else None

    def hub_paused(self, runner_id: str) -> bool:
        rows = self._store.all(select(hub_control.c.paused).where(hub_control.c.runner_id == runner_id))
        return bool(rows[0].paused) if rows else False

    def local_paused(self, runner_id: str) -> bool:
        rows = self._store.all(
            select(local_pause_facts.c.paused)
            .where(local_pause_facts.c.runner_id == runner_id)
            .order_by(local_pause_facts.c.id.desc())
            .limit(1)
        )
        return bool(rows[0].paused) if rows else False

    def last_daemon_liveness(self) -> datetime | None:
        rows = self._store.all(select(func.max(daemon_liveness.c.alive_at).label("alive_at")))
        return rows[0].alive_at if rows and rows[0].alive_at is not None else None

    def pause_parked_lease_ids(self) -> set[str]:
        return {str(r.lease_id) for r in self._store.all(PAUSE_PARKED_LEASE_IDS)}

    def record_daemon_liveness(self, *, runner_id: str, alive_at: datetime) -> None:
        with self._store.begin() as conn:
            existing = conn.execute(
                select(daemon_liveness.c.runner_id).where(daemon_liveness.c.runner_id == runner_id)
            ).one_or_none()
            if existing is None:
                conn.execute(daemon_liveness.insert().values(runner_id=runner_id, alive_at=alive_at))
            else:
                conn.execute(
                    daemon_liveness.update().where(daemon_liveness.c.runner_id == runner_id).values(alive_at=alive_at)
                )
        _log.debug("daemon liveness stamped", runner_id=runner_id)

    def set_hub_paused(self, runner_id: str, *, paused: bool, at: datetime) -> None:
        with self._store.begin() as conn:
            existing = conn.execute(
                select(hub_control.c.runner_id).where(hub_control.c.runner_id == runner_id)
            ).one_or_none()
            if existing is None:
                conn.execute(hub_control.insert().values(runner_id=runner_id, paused=paused, updated_at=at))
            else:
                conn.execute(
                    hub_control.update()
                    .where(hub_control.c.runner_id == runner_id)
                    .values(paused=paused, updated_at=at)
                )

    def record_local_pause(
        self, runner_id: str, *, paused: bool, at: datetime, by: str, report_kind: str, report_payload: str
    ) -> int:
        # Both inserts, one transaction: two would leave a `kill -9` window where the runner
        # has stopped claiming and the hub is never told (issue #43).
        with self._store.begin() as conn:
            conn.execute(local_pause_facts.insert().values(runner_id=runner_id, paused=paused, set_at=at, set_by=by))
            result = conn.execute(
                outbound_buffer.insert().values(
                    kind=report_kind, chunk_id=None, lease_id=None, payload=report_payload, created_at=at
                )
            )
        _log.info("local pause fact recorded", runner_id=runner_id, paused=paused, set_by=by, report=report_kind)
        key = result.inserted_primary_key
        return int(key[0]) if key is not None else 0

    def record_pause_park(self, *, lease_id: str, chunk_id: str, parked_at: datetime) -> None:
        with self._store.begin() as conn:
            conn.execute(pause_parks.insert().values(lease_id=lease_id, chunk_id=chunk_id, parked_at=parked_at))
        _log.info("chunk parked on operator pause", lease_id=lease_id, chunk_id=chunk_id)

    def record_pause_park_resume(self, *, lease_id: str, resumed_at: datetime) -> None:
        with self._store.begin() as conn:
            conn.execute(pause_park_resumes.insert().values(lease_id=lease_id, resumed_at=resumed_at))
        _log.info("pause park resumed", lease_id=lease_id)


def _conforms_pause_store(x: PauseStore) -> IWritePauseRepository:
    return x

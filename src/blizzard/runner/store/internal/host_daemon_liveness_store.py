"""SQLAlchemy adapter for the daemon-liveness repository seam (package-private).

Host-scoped: the beat is the process's, kept whether or not the store is bound to a runner yet, so its statements
carry no runner equality."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import func, select

from blizzard.foundation.logging import get_logger
from blizzard.runner.lifecycle.daemon_liveness import IWriteDaemonLivenessRepository
from blizzard.runner.store.schema import daemon_liveness
from blizzard.runner.store.scoped import RunnerStoreConnections

_log = get_logger("blizzard.runner.store")


class DaemonLivenessStore:
    """Read-write daemon-liveness adapter over the runner store engine."""

    def __init__(self, store: RunnerStoreConnections) -> None:
        self._store = store

    def last_daemon_liveness(self) -> datetime | None:
        rows = self._store.all(select(func.max(daemon_liveness.c.alive_at).label("alive_at")))
        return rows[0].alive_at if rows and rows[0].alive_at is not None else None

    def record_daemon_liveness(self, *, alive_at: datetime) -> None:
        with self._store.begin() as conn:
            existing = conn.execute(select(daemon_liveness.c.id).limit(1)).one_or_none()
            if existing is None:
                conn.execute(daemon_liveness.insert().values(alive_at=alive_at))
            else:
                conn.execute(
                    daemon_liveness.update().where(daemon_liveness.c.id == existing.id).values(alive_at=alive_at)
                )
        _log.debug("daemon liveness stamped")


def _conforms_daemon_liveness_store(x: DaemonLivenessStore) -> IWriteDaemonLivenessRepository:
    return x

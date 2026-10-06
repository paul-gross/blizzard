"""SQLAlchemy adapter for the runner-identity repository seam (package-private)."""

from __future__ import annotations

from sqlalchemy import delete, select

from blizzard.foundation.logging import get_logger
from blizzard.runner.hub.identity import IWriteRunnerIdentityRepository, RunnerIdentity
from blizzard.runner.store.errors import RunnerStoreConnections
from blizzard.runner.store.schema import runner_identity

_log = get_logger("blizzard.runner.store")


class RunnerIdentityStore:
    """Read-write adapter over the runner store's single identity row."""

    def __init__(self, store: RunnerStoreConnections) -> None:
        self._store = store

    def runner_identity(self) -> RunnerIdentity | None:
        rows = self._store.all(
            select(runner_identity.c.runner_id, runner_identity.c.runner_name, runner_identity.c.registered_at)
            .order_by(runner_identity.c.id.desc())
            .limit(1)
        )
        if not rows:
            return None
        r = rows[0]
        return RunnerIdentity(runner_id=str(r.runner_id), runner_name=str(r.runner_name), registered_at=r.registered_at)

    def record_runner_identity(self, identity: RunnerIdentity) -> None:
        with self._store.begin() as conn:
            conn.execute(delete(runner_identity))
            conn.execute(
                runner_identity.insert().values(
                    runner_id=identity.runner_id,
                    runner_name=identity.runner_name,
                    registered_at=identity.registered_at,
                )
            )
        _log.debug("runner identity recorded", runner_id=identity.runner_id, runner_name=identity.runner_name)


def _conforms_runner_identity_store(x: RunnerIdentityStore) -> IWriteRunnerIdentityRepository:
    return x

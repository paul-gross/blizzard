"""SQLAlchemy adapter for the runner-identity repository seam (package-private)."""

from __future__ import annotations

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
            self._store.select(
                runner_identity,
                runner_identity.c.runner_id,
                runner_identity.c.runner_name,
                runner_identity.c.registered_at,
                runner_identity.c.tenant_id,
                runner_identity.c.tenant_name,
            )
            .order_by(runner_identity.c.id.desc())
            .limit(1)
        )
        if not rows:
            return None
        r = rows[0]
        return RunnerIdentity(
            runner_id=str(r.runner_id),
            runner_name=str(r.runner_name),
            registered_at=r.registered_at,
            tenant_id=r.tenant_id,
            tenant_name=r.tenant_name,
        )

    def record_runner_identity(self, identity: RunnerIdentity) -> None:
        values = {
            "runner_name": identity.runner_name,
            "registered_at": identity.registered_at,
            "tenant_id": identity.tenant_id,
            "tenant_name": identity.tenant_name,
        }
        # Registration's adoption binds an unbound store; a bound one refuses another runner's id.
        self._store.bind(identity.runner_id)
        with self._store.begin() as conn:
            # The bound runner's row is updated in place, so the served-projects answer's marker on it stays.
            kept = conn.execute(self._store.update(runner_identity).values(**values)).rowcount
            if not kept:
                conn.execute(self._store.insert(runner_identity).values(**values))
        _log.debug(
            "runner identity recorded",
            runner_id=identity.runner_id,
            runner_name=identity.runner_name,
            tenant_id=identity.tenant_id,
        )


def _conforms_runner_identity_store(x: RunnerIdentityStore) -> IWriteRunnerIdentityRepository:
    return x

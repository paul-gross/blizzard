"""SQLAlchemy adapter for the runner-identity repository seam (package-private)."""

from __future__ import annotations

from sqlalchemy import Connection

from blizzard.foundation.logging import get_logger
from blizzard.runner.hosting.ports import IAdmissionHandle, IWriteAdmittedIdentity
from blizzard.runner.hub.identity import (
    IRunnerIdentityStore,
    IWriteRunnerIdentityRepository,
    RunnerIdentity,
)
from blizzard.runner.store.internal.admission_handle import admission_of
from blizzard.runner.store.schema import runner_identity
from blizzard.runner.store.scoped import RunnerAlreadyBound, RunnerStoreConnections, RunnerUnbound

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
        self._bound(identity)
        with self._store.begin() as conn:
            self._write(conn, identity)
        _log.debug(
            "runner identity recorded",
            runner_id=identity.runner_id,
            runner_name=identity.runner_name,
            tenant_id=identity.tenant_id,
        )

    def record_admitted_locked(self, handle: IAdmissionHandle, identity: RunnerIdentity) -> None:
        self._bound(identity)
        self._write(admission_of(handle).conn, identity)

    def _bound(self, identity: RunnerIdentity) -> None:
        bound = self._store.runner_id
        if bound is None:
            raise RunnerUnbound(runner_identity.name)
        if bound != identity.runner_id:
            raise RunnerAlreadyBound(bound, identity.runner_id)

    def _write(self, conn: Connection, identity: RunnerIdentity) -> None:
        """The bound runner's row, updated in place when held — so the served-projects answer's marker on it stays —
        on ``conn``'s open transaction."""
        values = {
            "runner_name": identity.runner_name,
            "registered_at": identity.registered_at,
            "tenant_id": identity.tenant_id,
            "tenant_name": identity.tenant_name,
        }
        if not conn.execute(self._store.update(runner_identity).values(**values)).rowcount:
            conn.execute(self._store.insert(runner_identity).values(**values))


_runner_identity_store_conforms: type[IWriteRunnerIdentityRepository] = RunnerIdentityStore

_runner_identity_bundle_conforms: type[IRunnerIdentityStore] = RunnerIdentityStore

_admitted_identity_conforms: type[IWriteAdmittedIdentity] = RunnerIdentityStore
